"""Runtime/benchmark parity, evidence boundaries, and industrial SSE behavior."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.knowledge_bases import AnswerPipeline, get_kb
from backend.knowledge_bases.industrial_qa import SPEC
from backend.knowledge_bases.industrial_qa.answering import IndustrialAnswerWriter, WrittenAnswer
from backend.knowledge_bases.industrial_qa.benchmark.adapter import ScopedRetrieval
from backend.knowledge_bases.industrial_qa.config import DEFAULT_PATH, ENV_VAR, load_config, runner_config
from backend.knowledge_bases.industrial_qa.pipeline import IndustrialQaPipeline, build_retriever, build_rewriter
from backend.knowledge_bases.industrial_qa.query_rewrite import RewrittenQuery, check
from backend.knowledge_bases.industrial_qa.retrieval import RetrievalConfig, RetrievalResult, Source
from backend.knowledge_bases.industrial_qa.service import IndustrialQaService, answer_with_citations
from backend.src.agents.agent.service import (
    InvalidKnowledgeCitation, KnowledgeAgentService, KnowledgeAgentUnavailable, KnowledgeAgentUpstreamError,
)
from backend.src.agents.rag.store import ChunkHit, rrf_fuse
from backend.src.services import llm
from backend.src.services.knowledge_extraction import KnowledgeType
from backend.src.storage import KnowledgeRecord, StorageError


def record(**context):
    return KnowledgeRecord(subject='encoder', claim='Check CN2 before replacing the amplifier.',
        type=KnowledgeType.RULE, evidence_id=uuid4(), context=context)


def passage(doc_id='melservo_je_p0021_c01', text='Connection procedure: isolate power first.', **metadata):
    return ChunkHit(doc_id+'#0', doc_id, 0, 'Troubleshooting > Connectors', text, 1.0, metadata=metadata)


def stores(records=(), passages=(), neighbors=()):
    knowledge = SimpleNamespace(search_knowledge=AsyncMock(return_value=[
        SimpleNamespace(record=r, confident=True) for r in records]), aclose=AsyncMock())
    documents = SimpleNamespace(search=AsyncMock(return_value=list(passages)),
        adjacent_chunks=AsyncMock(return_value=list(neighbors)), aclose=AsyncMock())
    return knowledge, documents


def written(sources=('K1','D1'), answer='Isolate power, then check CN2.'):
    return json.dumps({'answer': answer, 'sources': list(sources),
        'checklist': [{'point': 'Check connectors first', 'sources': list(sources)}] if sources else []})


class PipelineConfigTests(unittest.TestCase):
    def write(self, directory, text):
        path = Path(directory) / 'config.toml'
        path.write_text(DEFAULT_PATH.read_text().replace('"rerank.md"', json.dumps(str(DEFAULT_PATH.with_name('rerank.md'))))
                        .replace('"answer.md"', json.dumps(str(DEFAULT_PATH.with_name('answer.md'))))
                        .replace('"query.md"', json.dumps(str(DEFAULT_PATH.with_name('query.md')))) + text)
        return path

    def test_packaged_config_matches_code_defaults(self):
        with patch.dict(os.environ, {ENV_VAR: ''}):
            config = load_config()
        self.assertEqual(config.path, DEFAULT_PATH)
        # The packaged file pins the frozen runs' settings that the code leaves to the environment.
        self.assertEqual(config.retrieval, RetrievalConfig(min_vector_similarity=0.6))
        self.assertEqual((config.rerank.max_tokens, config.answer.max_tokens, config.choice.max_tokens), (2048, 8192, 1024))
        self.assertEqual((config.extraction.env_prefix, config.extraction.max_tokens, config.extraction.thinking),
                         ('EXTRACTION', 8192, 'disabled'))
        self.assertEqual((config.ingestion.verify, config.ingestion.consolidate), ('quotes', True))
        self.assertEqual((config.conversation.history_characters, config.conversation.history_turns), (6000, 5))
        self.assertEqual(config.prompts['rerank'], DEFAULT_PATH.with_name('rerank.md').read_text().strip())
        provenance = config.provenance()
        self.assertEqual(provenance['retrieval']['rerank_mode'], 'hybrid')
        self.assertNotIn('api_key', json.dumps(provenance).lower())

    def test_env_selects_file_and_unknown_or_invalid_settings_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, '')
            path.write_text(path.read_text().replace('rerank_mode = "hybrid"', 'rerank_mode = "llm"'))
            with patch.dict(os.environ, {ENV_VAR: str(path)}):
                self.assertEqual(load_config().retrieval.rerank_mode, 'llm')
            for broken in ('\n[llm.judge]\nenv_prefix = "X"\nmax_tokens = 1\n', '\ntop_k = 3\n'):
                with self.assertRaises(ValueError):
                    load_config(self.write(directory, broken))
            path.write_text(path.read_text().replace('k = 8', 'k = 8\nrerank_top = 4'))
            with self.assertRaises(ValueError):
                load_config(path)
            path.write_text(path.read_text().replace('rerank_mode = "llm"', 'rerank_mode = "cross"'))
            with self.assertRaises(ValueError):
                load_config(path)

    def test_toml_llm_settings_override_environment(self):
        env = {'OPENROUTER_API_KEY': 'test', 'OPENROUTER_MODEL': 'env-model', 'ANSWER_THINKING': 'enabled',
               'EXTRACTION_MODEL': 'env-extraction', 'EXTRACTION_THINKING': 'enabled'}
        with patch.dict(os.environ, env):
            config = load_config(DEFAULT_PATH)
            client = config.answer.client('written_answer', WrittenAnswer)
            service, provenance = config.ingestion_service(SPEC, object())
            resolved = config.provenance()['llm']
        self.assertEqual((client.model_name, client.extra_body), ('deepseek-v4.1-flash', {'thinking': {'type': 'disabled'}}))
        self.assertEqual({key: provenance[key] for key in ('model', 'max_tokens', 'thinking')},
                         {'model': 'deepseek-v4.1-flash', 'max_tokens': 8192, 'thinking': 'disabled'})
        self.assertEqual(provenance['version'], SPEC.extraction_version)
        self.assertEqual(service._verifier.method, 'quotes')
        self.assertIsNotNone(service._consolidator)
        self.assertEqual(resolved['verify']['thinking'], None)
        self.assertNotIn('test', json.dumps(resolved))

    def test_ingestion_settings_fall_back_to_voice_environment(self):
        env = {'VOICE_VERIFY': 'off', 'VOICE_CONSOLIDATE': 'false'}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, env):
            self.assertEqual((load_config(DEFAULT_PATH).ingestion.verify, load_config(DEFAULT_PATH).ingestion.consolidate),
                             ('quotes', True))
            path = self.write(directory, '')
            path.write_text(path.read_text().replace('verify = "quotes"', '').replace('consolidate = true', ''))
            ingestion = load_config(path).ingestion
            self.assertEqual((ingestion.verify, ingestion.consolidate, ingestion.validation_retries), ('off', False, 0))
            with patch.dict(os.environ, {'VOICE_VERIFY': '', 'VOICE_CONSOLIDATE': ''}):
                self.assertEqual((load_config(path).ingestion.verify, load_config(path).ingestion.consolidate),
                                 ('quotes', True))
            for broken in ({'VOICE_VERIFY': 'maybe'}, {'VOICE_CONSOLIDATE': 'perhaps'}):
                with patch.dict(os.environ, broken), self.assertRaises(ValueError):
                    load_config(path)

    def test_voice_capture_uses_the_industrial_ingestion_settings(self):
        from backend.api.voice_capture import default_ingestion_factory
        with patch('backend.knowledge_bases.industrial_qa.config.PipelineConfig.ingestion_service',
                   return_value=('service', {'version': 'v'})) as built:
            self.assertEqual(default_ingestion_factory(SPEC, 'store'), ('service', {'version': 'v'}))
        built.assert_called_once_with(SPEC, 'store')

    def test_runner_flags_override_only_when_given(self):
        args = SimpleNamespace(config=DEFAULT_PATH, k=None, per_conversation=0)
        config = runner_config(args)
        self.assertEqual((args.k, config.retrieval.k, config.retrieval.per_conversation), (8, 8, 0))
        self.assertEqual(load_config(DEFAULT_PATH).retrieval.per_conversation, 3)


class IndustrialPipelineTests(unittest.IsolatedAsyncioTestCase):
    def test_registered_official_policy_and_compatibility(self):
        self.assertIs(get_kb('industrial_qa'), SPEC)
        self.assertIs(SPEC.answer_pipeline, AnswerPipeline.INDUSTRIAL_QA)
        self.assertIs(get_kb('maintenance').answer_pipeline, AnswerPipeline.AGENT)
        with self.assertRaises(ValueError):
            replace(SPEC, retrieval='documents')

    def test_fusion_preserves_manual_metadata(self):
        chunk = passage(reference_doc_id='melservo_je', page_start=21, page_end=23)
        fused = rrf_fuse([chunk], [chunk])
        self.assertEqual(fused[0].metadata, chunk.metadata)
        self.assertIn('p. 21-23', Source('D1', passage=fused[0]).text)

    async def test_runtime_and_benchmark_use_identical_evidence_without_public_chunks(self):
        rec = record(machine_id='MR-JE-AX391', machine_model='MR-JE-70A')
        chunk, neighbor = passage(), passage('melservo_je_p0022_c01', text='Inspect the CN2 latch.')
        knowledge, documents = stores([rec], [chunk], [neighbor])
        invoke = AsyncMock(return_value='{"labels":["K1","D2","D1"]}')
        with patch.object(llm, 'chat_client_from_env', return_value=object()), patch.object(llm, 'ainvoke', invoke):
            core = build_retriever()
            retrieved = await core.retrieve('MR-JE-AX391 connectors', knowledge, documents)
            adapter = ScopedRetrieval(scope=None)
            sources, _ = await adapter('MR-JE-AX391 connectors', knowledge, documents, {'run_id':'run'},
                SimpleNamespace(k=8, per_conversation=3), {}, {}, {})
        self.assertEqual({s.label:s.text for s in retrieved.sources}, {k:v['text'] for k,v in sources.items()})
        self.assertEqual(len(retrieved.sources), 3)
        self.assertNotIn('benchmark_run_id', json.dumps(knowledge.search_knowledge.call_args_list[0].kwargs))
        self.assertIn('benchmark_run_id', json.dumps(knowledge.search_knowledge.call_args_list[1].kwargs))
        self.assertIn('MR-JE-AX391', json.dumps(knowledge.search_knowledge.call_args_list[0].kwargs))
        documents.adjacent_chunks.assert_awaited()
        # Runtime has no access to files, public chunks, or benchmark mappings.
        self.assertNotIn('run', invoke.call_args_list[0].args[1])

    async def test_family_manual_filter_and_neighbor_boundary(self):
        fx = passage('fx5_p0001_c01')
        main = passage()
        other_section = replace(passage('melservo_je_p0022_c01'), title='Different > Section')
        distant = passage('melservo_je_p0200_c01')
        good = passage('melservo_je_p0023_c01')
        knowledge, documents = stores([], [fx, main], [other_section, distant, good])
        invoke = AsyncMock(return_value='{"labels":["D1","D2"]}')
        with patch.object(llm, 'ainvoke', invoke):
            result = await build_retriever(ranker=object()).retrieve('MR-JE connections', knowledge, documents)
        self.assertEqual([s.passage.doc_id for s in result.sources], [main.doc_id, good.doc_id])
        self.assertNotIn(fx.doc_id, '\n'.join(s.text for s in result.sources))
        self.assertEqual(knowledge.search_knowledge.call_args.kwargs['min_vector_similarity'], 0.6)
        unset = load_config(DEFAULT_PATH).with_retrieval(min_vector_similarity=None)
        with patch.object(llm, 'ainvoke', AsyncMock(return_value='{"labels":["D1"]}')):
            await build_retriever(unset, ranker=object()).retrieve('MR-JE connections', knowledge, documents)
        self.assertNotIn('min_vector_similarity', knowledge.search_knowledge.call_args.kwargs)

    async def test_generic_queries_cap_conversations_but_exact_asset_retains_complementary_records(self):
        recs = [record(machine_id='MR-JE-AX391', machine_model='MR-JE') for _ in range(4)]
        for rec in recs:
            rec.evidence_id = recs[0].evidence_id
        knowledge, documents = stores(recs)
        with patch.object(llm, 'ainvoke', AsyncMock(side_effect=['{"labels":["K1","K2","K3"]}',
                                                               '{"labels":["K4","K1","K2","K3"]}'])):
            core = build_retriever(ranker=object())
            generic = await core.retrieve('MR-JE connector checks', knowledge, documents)
            exact = await core.retrieve('MR-JE-AX391 connector checks', knowledge, documents)
        self.assertEqual((len(generic.sources),len(exact.sources)),(3,4))

    async def test_cross_encoder_prefilter_keeps_top_per_source_and_benchmark_quota(self):
        recs = [record().model_copy(update={'claim': f'claim {i}'}) for i in range(3)]
        chunks = [passage(f'melservo_je_p00{i}0_c01', text=f'manual {i}') for i in range(3)]
        knowledge, documents = stores(recs, chunks)
        scores = {'claim 0': .1, 'claim 1': .9, 'claim 2': .5, 'manual 0': .2, 'manual 1': .3, 'manual 2': .8}
        prefilter = SimpleNamespace(score=AsyncMock(side_effect=lambda query, texts:
            [scores[text.splitlines()[-1]] for text in texts]))
        invoke = AsyncMock(return_value='{"labels":["K1","D1","K2"]}')
        with patch.object(llm, 'ainvoke', invoke):
            core = build_retriever(load_config().with_retrieval(rerank_mode='llm', prefilter_knowledge=2, prefilter_manual=1),
                ranker=object(), prefilter=prefilter)
            result = await core.retrieve('encoder', knowledge, documents)
            await ScopedRetrieval(scope=None, retriever=core)('encoder', knowledge, documents,
                {'run_id': 'run'}, SimpleNamespace(k=8, per_conversation=3), {}, {}, {})
        # The cross-encoder never sees the ephemeral labels it reorders.
        self.assertFalse(any(text.startswith('[') for text in prefilter.score.call_args.args[1]))
        self.assertEqual((result.candidate_pool, result.candidate_count), (6, 3))
        self.assertEqual([s.text.splitlines()[-1] for s in result.sources], ['claim 1', 'manual 2', 'claim 2'])
        for prompt in (call.args[1] for call in invoke.call_args_list):
            self.assertEqual(prompt.count('[K'), 2)
            self.assertEqual(prompt.count('[D'), 1)

    async def test_hybrid_rerank_mode_cuts_in_search_order_but_keeps_procedure_neighbors(self):
        recs = [record().model_copy(update={'claim': f'claim {i}'}) for i in range(3)]
        chunks = [passage('melservo_je_p0000_c01', text='Connection procedure 0'),
                  *(passage(f'melservo_je_p00{i}0_c01', text=f'manual {i}') for i in (1, 2))]
        neighbor = passage('melservo_je_p0001_c01', text='procedure step 2')
        knowledge, documents = stores(recs, chunks, [neighbor])
        invoke = AsyncMock(return_value='{"labels":["K2","D2","D1"]}')
        with patch.object(llm, 'ainvoke', invoke):
            core = build_retriever(load_config().with_retrieval(
                rerank_mode='hybrid', prefilter_knowledge=2, prefilter_manual=1), ranker=object())
            result = await core.retrieve('encoder', knowledge, documents)
            await ScopedRetrieval(scope=None, retriever=core)('encoder', knowledge, documents,
                {'run_id': 'run'}, SimpleNamespace(k=8, per_conversation=3), {}, {}, {})
        self.assertEqual((result.candidate_pool, result.candidate_count), (4, 4))
        self.assertEqual([s.text.splitlines()[-1] for s in result.sources],
                         ['claim 1', 'procedure step 2', 'Connection procedure 0'])
        for prompt in (call.args[1] for call in invoke.call_args_list):
            self.assertNotIn('claim 2', prompt)
            self.assertNotIn('manual 1', prompt)
            self.assertIn('procedure step 2', prompt)
        with self.assertRaises(ValueError):
            RetrievalConfig(rerank_mode='cross_encoder')

    async def test_second_hop_searches_manual_with_record_wording_and_is_off_by_default(self):
        question = 'MR-JE-AX391 servo will not respond and shows no alarm'
        recs = [record(machine_id='MR-JE-AX391').model_copy(update={'subject': 'MR-JE amplifier',
                    'claim': 'On MR-JE-AX391 at Line 4, copy the rotary switch and Pr. PC70 before power-up.'}),
                record(machine_id='MR-JE-AX391').model_copy(update={'claim': 'Check the CN2 latch.'})]
        first, station, latch = (passage(f'melservo_je_p00{page}_c01', text=f'manual {page}') for page in (21, 77, 78))
        other_manual = passage('fx5_p0001_c01', text='fx5 manual')

        async def search(query, k):
            if 'PC70' in query:
                return [station, first, other_manual]
            if 'CN2' in query:
                return [latch, station]
            return [first]
        knowledge, documents = stores(recs)
        documents.search = AsyncMock(side_effect=search)
        core = build_retriever(ranker=object())
        candidates = (await core.candidates(question, knowledge, documents)).sources
        self.assertEqual(documents.search.await_count, 1)
        self.assertEqual([s.passage.doc_id for s in candidates.values() if s.passage], [first.doc_id])

        documents.search.reset_mock()
        enabled = replace(load_config().retrieval, second_hop_records=2, second_hop_per_record=3, second_hop_manual=4)
        candidates = (await core.candidates(question, knowledge, documents, config=enabled)).sources
        hops = [call.args[0] for call in documents.search.await_args_list[1:]]
        self.assertEqual(len(hops), 2)
        self.assertEqual({call.kwargs['k'] for call in documents.search.await_args_list[1:]}, {3})
        # Record wording, scoped like the first-hop manual query: family kept, asset and line removed.
        self.assertTrue(hops[0].startswith('MR-JE ') and 'PC70' in hops[0])
        self.assertNotIn('AX391', hops[0])
        self.assertNotIn('Line 4', hops[0])
        # First-hop order is kept; new chunks interleave by rank, skip duplicates and other manuals.
        self.assertEqual({label: s.passage.doc_id for label, s in candidates.items() if s.passage},
                         {'D1': first.doc_id, 'D2': station.doc_id, 'D3': latch.doc_id})

        capped = replace(enabled, second_hop_manual=1)
        candidates = (await core.candidates(question, knowledge, documents, config=capped)).sources
        self.assertEqual([s.passage.doc_id for s in candidates.values() if s.passage], [first.doc_id, station.doc_id])
        for invalid in ({'second_hop_records': -1}, {'second_hop_per_record': 0}, {'second_hop_manual': 0}):
            with self.assertRaises(ValueError):
                RetrievalConfig(**invalid)

    async def test_second_hop_in_hybrid_mode_uses_cut_records_and_adds_past_the_manual_cut(self):
        question = 'MR-JE-AX391 servo will not respond and shows no alarm'
        recs = [record(machine_id='MR-JE-AX391').model_copy(update={'claim': 'Copy Pr. PC70 before power-up.'}),
                record(machine_id='MR-JE-AX391').model_copy(update={'claim': 'Check the CN2 latch.'})]
        first, cut, station, latch = (passage(f'melservo_je_p00{page}_c01', text=f'manual {page}')
                                      for page in (21, 22, 77, 78))

        async def search(query, k):
            if 'PC70' in query:
                return [station]
            if 'CN2' in query:
                return [latch]
            return [first, cut]
        knowledge, documents = stores(recs)
        documents.search = AsyncMock(side_effect=search)
        hybrid = replace(load_config().retrieval, rerank_mode='hybrid', prefilter_knowledge=1, prefilter_manual=1,
                         second_hop_records=2)
        candidates = (await build_retriever(ranker=object()).candidates(
            question, knowledge, documents, config=hybrid)).sources
        # Only the record kept by the hybrid cut steers a second hop.
        self.assertEqual(documents.search.await_count, 2)
        self.assertEqual([s.record.claim for s in candidates.values() if s.record], [recs[0].claim])
        # The hybrid cut drops the second first-hop chunk; the second-hop chunk joins after it.
        self.assertEqual([s.passage.doc_id for s in candidates.values() if s.passage], [first.doc_id, station.doc_id])

    def rewriting(self, mode):
        config = load_config()
        config = replace(config, retrieval=replace(config.retrieval, query_rewrite=mode))
        return config, build_rewriter(config, client=object())

    async def test_query_rewrite_is_off_by_default_and_auto_fires_only_on_follow_ups_and_other_languages(self):
        self.assertEqual(load_config().retrieval.query_rewrite, 'off')
        self.assertIsNone(build_retriever(ranker=object()).rewriter)
        with self.assertRaises(ValueError):
            RetrievalConfig(query_rewrite='sometimes')
        _, rewriter = self.rewriting('auto')
        history = [('MR-JE-AX391 CN2 checks', 'Isolate power, then check CN2.')]
        with patch.object(llm, 'ainvoke', AsyncMock(return_value='not json')) as invoke:
            english = await rewriter.plan('MR-JE-AX391 shows AL. 16.3', history, mode='auto')
            scoped = await rewriter.plan('What about FX5U-32MT?', history, mode='auto')
            self.assertEqual(invoke.await_count, 0)
            follow_up = await rewriter.plan('Why first?', history, mode='auto')
            language = await rewriter.plan('Máy MR-JE-AX391 báo lỗi AL. 16.3 thì kiểm tra gì?', (), mode='auto')
        self.assertEqual((english.status, scoped.status), ('skipped', 'skipped'))
        self.assertEqual((english.base, scoped.base), ('MR-JE-AX391 shows AL. 16.3', 'What about FX5U-32MT?'))
        self.assertEqual(follow_up.base, 'MR-JE-AX391 CN2 checks\nFollow-up question: Why first?')
        # A failed rewrite falls back to the original branch.
        self.assertEqual([(p.reason, p.status, p.rewritten) for p in (follow_up, language)],
                         [('follow-up', 'failed', None), ('language', 'failed', None)])
        self.assertEqual(follow_up.rerank_question, follow_up.base)
        self.assertIn('User: MR-JE-AX391 CN2 checks', invoke.await_args_list[0].args[1])
        self.assertIn('## History\n(none)', invoke.await_args_list[1].args[1])

    async def test_rewrite_adds_interleaved_candidates_and_keeps_the_original_branch(self):
        question = 'Máy MR-JE-AX391 báo AL. 16.3 sau khi cắm lại CN2, kiểm tra gì?'
        rewritten = {'standalone_question': 'What should be checked when MR-JE-AX391 shows AL.16.3 after reconnecting CN2?',
                     'knowledge_query': 'MR-JE-AX391 AL.16.3 after CN2 reconnection check',
                     'manual_query': 'MR-JE alarm 16.3 encoder cable CN2 reconnection check'}
        recs = [record(machine_id='MR-JE-AX391').model_copy(update={'claim': f'claim {n}'}) for n in range(3)]
        chunks = [passage(f'melservo_je_p00{n}_c01', text=f'manual {n}') for n in (21, 22, 23)]

        async def search_knowledge(query, **_):
            hits = [recs[1], recs[2]] if query == rewritten['knowledge_query'] else [recs[0], recs[1]]
            return [SimpleNamespace(record=r, confident=True) for r in hits]

        async def search(query, k):
            return [chunks[1], chunks[2]] if 'encoder cable' in query else [chunks[0], chunks[1]]
        knowledge, documents = stores()
        knowledge.search_knowledge = AsyncMock(side_effect=search_knowledge)
        documents.search = AsyncMock(side_effect=search)
        config, rewriter = self.rewriting('auto')
        core = build_retriever(config, ranker=object(), rewriter=rewriter)
        with patch.object(llm, 'ainvoke', AsyncMock(return_value=json.dumps(rewritten))):
            found = await core.candidates(question, knowledge, documents)
        assets, family, candidates, plan = found.assets, found.family, found.sources, found.plan
        self.assertEqual((plan.reason, plan.status), ('language', 'used'))
        # Scope comes from the catalog on the user's own words.
        self.assertEqual((list(assets), family), (['MR-JE-AX391'], 'MR-JE'))
        self.assertEqual([c.args[0] for c in knowledge.search_knowledge.await_args_list],
                         [question, rewritten['knowledge_query']])
        self.assertEqual([s.record.claim for s in candidates.values() if s.record], ['claim 0', 'claim 1', 'claim 2'])
        self.assertEqual([s.passage.doc_id for s in candidates.values() if s.passage], [c.doc_id for c in chunks])
        # The language trigger keeps the reranker on the user's question.
        self.assertEqual(plan.rerank_question, question)
        self.assertNotIn('AX391', documents.search.await_args_list[1].args[0])

        dropped = {**rewritten, 'manual_query': 'MR-JE encoder cable CN2 reconnection check'}
        invented = {**rewritten, 'knowledge_query': rewritten['knowledge_query'] + ' MR-JE-AX392'}
        for response, detail in ((dropped, 'manual_query dropped 16.3'), (invented, 'invented asset MR-JE-AX392')):
            knowledge.search_knowledge.reset_mock()
            with patch.object(llm, 'ainvoke', AsyncMock(return_value=json.dumps(response))):
                found = await core.candidates(question, knowledge, documents)
            candidates, plan = found.sources, found.plan
            self.assertEqual((plan.status, plan.detail), ('rejected', detail))
            self.assertEqual(knowledge.search_knowledge.await_count, 1)
            self.assertEqual([s.record.claim for s in candidates.values() if s.record], ['claim 0', 'claim 1'])
        # The manual search adds the detected family itself, so the rewrite may omit it.
        family_only = RewrittenQuery(standalone_question='How do we recover when our FX5 PLC cannot reach the SD card?',
            knowledge_query='FX5 PLC cannot reach SD card recovery', manual_query='SD memory card access failure recovery')
        self.assertIsNone(check(family_only, 'Our FX5 PLC cannot reach the SD card. How do we recover?', ()))

    async def test_follow_up_rewrite_steers_reranker_but_writer_keeps_the_user_question(self):
        rec = record(machine_id='MR-JE-AX391', machine_model='MR-JE')
        knowledge, documents = stores([rec])
        rewritten = {'standalone_question': 'Why should CN2 on MR-JE-AX391 be checked first?',
                     'knowledge_query': 'MR-JE-AX391 why check CN2 first',
                     'manual_query': 'MR-JE why check CN2 connector first'}
        config, rewriter = self.rewriting('auto')
        with patch.object(llm, 'ainvoke', AsyncMock(side_effect=[
                '{"labels":["K1"]}', written(('K1',)),
                json.dumps(rewritten), '{"labels":["K1"]}', written(('K1',))])) as invoke:
            service = IndustrialQaService(SPEC, knowledge, documents=documents,
                pipeline=IndustrialQaPipeline(retriever=build_retriever(config, ranker=object(), rewriter=rewriter),
                                              writer=IndustrialAnswerWriter(object())))
            [e async for e in service.start_stream('MR-JE-AX391 CN2 checks', thread_id='a').events]
            second = [e async for e in service.start_stream('Why first?', thread_id='a').events]
        self.assertEqual(rewriter.calls, 1)
        self.assertEqual([c.args[0] for c in knowledge.search_knowledge.await_args_list[1:]],
                         ['MR-JE-AX391 CN2 checks\nFollow-up question: Why first?', rewritten['knowledge_query']])
        self.assertIn(rewritten['standalone_question'], invoke.await_args_list[3].args[1])
        self.assertNotIn(rewritten['standalone_question'], invoke.await_args_list[4].args[1])
        self.assertIn('Why first?', invoke.await_args_list[4].args[1])
        started, completed = (next(e.data for e in second if e.event == kind) for kind in ('tool.started', 'tool.completed'))
        self.assertEqual(started['input'], {'query': 'MR-JE-AX391 CN2 checks\nFollow-up question: Why first?'})
        self.assertEqual(completed['output']['query_rewrite'], {'reason': 'follow-up', 'status': 'used'})
        self.assertNotIn(rewritten['knowledge_query'], json.dumps([e.data for e in second]))

    async def test_unknown_labels_and_empty_rankings_fail(self):
        knowledge, documents = stores([record()])
        for response in ('{"labels":["K99"]}', '{"labels":[]}'):
            with patch.object(llm, 'ainvoke', AsyncMock(return_value=response)), self.assertRaises(llm.LLMResponseError):
                await build_retriever(ranker=object()).retrieve('encoder', knowledge, documents)

    async def test_no_candidates_skips_ranker_and_writer_can_abstain(self):
        knowledge, documents = stores()
        with patch.object(llm, 'ainvoke', AsyncMock(return_value=written((), 'Insufficient evidence.'))) as invoke:
            pipeline = IndustrialQaPipeline(retriever=build_retriever(ranker=object()),
                writer=IndustrialAnswerWriter(object()))
            result = await pipeline.retrieve('encoder',knowledge,documents)
            self.assertEqual(result.sources, ())
            invoke.assert_not_awaited()
            answer,_ = await pipeline.write('encoder', result)
        self.assertEqual(answer.sources, [])

    async def test_writer_validates_sources_and_does_not_substitute_question_placeholders(self):
        writer = IndustrialAnswerWriter(object())
        invoke = AsyncMock(return_value=written(('K1',)))
        with patch.object(llm, 'ainvoke', invoke):
            await writer.write('Question literal {SOURCES}', {'K1':'[K1] Evidence literal {HISTORY}'}, history='Past turn')
        prompt = invoke.call_args.args[1]
        self.assertIn('Question literal {SOURCES}',prompt)
        self.assertIn('Evidence literal {HISTORY}',prompt)
        with patch.object(llm, 'ainvoke', AsyncMock(return_value=written(('K99',)))), self.assertRaises(llm.LLMResponseError):
            await writer.write('Question', {'K1':'Evidence'})
        with patch.object(llm, 'ainvoke', AsyncMock(return_value=written((), '  '))), self.assertRaises(llm.LLMResponseError):
            await writer.write('Question', {})

    async def test_default_benchmark_scope_includes_reused_run_ids(self):
        knowledge, documents = stores([record()])
        with patch.object(llm, 'chat_client_from_env', return_value=object()), \
                patch.object(llm, 'ainvoke', AsyncMock(return_value='{"labels":["K1"]}')):
            adapter = ScopedRetrieval()
            await adapter('encoder', knowledge, documents, {'run_id':'new','reused_run_ids':['old']},
                SimpleNamespace(k=8,per_conversation=3), {}, {}, {})
        filters=knowledge.search_knowledge.call_args.kwargs['filters']
        self.assertIn('new', json.dumps(filters))
        self.assertIn('old', json.dumps(filters))

    def test_citations_use_actual_ids_and_reject_unretrieved_ids(self):
        rec, chunk = record(), passage()
        result = RetrievalResult((Source('K1',record=rec),Source('D1',passage=chunk)))
        text, citations = answer_with_citations(WrittenAnswer.model_validate_json(written()),result)
        self.assertIn(f'[K:{rec.id}]',text)
        self.assertIn(f'[D:{chunk.doc_id}]',text)
        self.assertEqual(len(citations),2)
        for prose in (f'Wrong [K:{uuid4()}]', 'Wrong [D:unknown]', 'Wrong [K2]',
                      'Wrong [K:bogus]', 'Wrong [D:bad id]'):
            with self.assertRaises(InvalidKnowledgeCitation):
                answer_with_citations(WrittenAnswer.model_validate_json(written(answer=prose)),result)

    def test_industrial_citations_keep_stored_case_card_for_ui(self):
        card = {"context": "Line 5", "observation": "Intermittent alarm",
                "reasoning": "Welder suspected, not confirmed", "action": "Planned checks only.",
                "outcome": None, "evidence": [{"turn_id": "T03", "quote": "Check first."}]}
        rec = record(machine_id="MR-JE-AX205", case_card=card, benchmark_run_id="internal")
        rec.type = KnowledgeType.CASE
        result = RetrievalResult((Source("K1", record=rec),))
        _, (citation,) = answer_with_citations(WrittenAnswer.model_validate_json(written(("K1",))), result)
        self.assertEqual(citation.case_card, card)
        self.assertEqual(citation.scope, {"machine_id": "MR-JE-AX205"})
        self.assertEqual(citation.status, "draft")

    async def test_service_history_is_scoped_and_deleted_and_trace_hides_claims(self):
        rec = record(machine_id='MR-JE-AX391',machine_model='MR-JE')
        knowledge,documents=stores([rec])
        with patch.object(llm,'ainvoke',AsyncMock(side_effect=[
            '{"labels":["K1"]}', written(('K1',)), '{"labels":["K1"]}', written(('K1',)),
            '{"labels":["K1"]}', written(('K1',))])) as invoke:
            service=IndustrialQaService(SPEC,knowledge,documents=documents,
                pipeline=IndustrialQaPipeline(retriever=build_retriever(ranker=object()),writer=IndustrialAnswerWriter(object())))
            first=[e async for e in service.start_stream('MR-JE-AX391 CN2 checks',thread_id='a').events]
            second=[e async for e in service.start_stream('Why first?',thread_id='a').events]
            third=[e async for e in service.start_stream('Why first?',thread_id='b').events]
        self.assertEqual([e.sequence for e in first],list(range(1,len(first)+1)))
        self.assertEqual(first[-1].event,'run.completed')
        self.assertIn('MR-JE-AX391', knowledge.search_knowledge.call_args_list[1].args[0])
        self.assertEqual(knowledge.search_knowledge.call_args_list[2].args[0],'Why first?')
        self.assertIn('Assistant: Isolate power',invoke.call_args_list[3].args[1])
        self.assertNotIn('Assistant: Isolate power',invoke.call_args_list[5].args[1])
        traces=json.dumps([e.data for e in first if e.event.startswith('tool.')])
        self.assertNotIn(rec.claim,traces)
        await service.delete_thread('a')
        self.assertNotIn('a',service._threads)
        await service.aclose()
        documents.aclose.assert_not_awaited() # caller owns an injected store

    async def test_inline_labels_stay_in_place_and_are_not_kept_in_history(self):
        rec,chunk=record(),passage()
        knowledge,documents=stores([rec],[chunk])
        inline='Check CN2 first [K1]. Isolate power before touching it [D1].'
        with patch.object(llm,'ainvoke',AsyncMock(side_effect=['{"labels":["K1","D1"]}',written(('K1','D1'),inline)])):
            service=IndustrialQaService(SPEC,knowledge,documents=documents,
                pipeline=IndustrialQaPipeline(retriever=build_retriever(ranker=object()),writer=IndustrialAnswerWriter(object())))
            events=[e async for e in service.start_stream('encoder',thread_id='a').events]
        text=next(e.data['text'] for e in events if e.event=='answer.delta')
        self.assertEqual(text,f'Check CN2 first [K:{rec.id}]. Isolate power before touching it [D:{chunk.doc_id}].')
        self.assertEqual(service._threads['a'].history,
            [('encoder','Check CN2 first. Isolate power before touching it.')])

    async def test_failure_never_emits_or_saves_invalid_answer(self):
        rec=record()
        knowledge,documents=stores([rec])
        with patch.object(llm,'ainvoke',AsyncMock(side_effect=['{"labels":["K1"]}',written(('K1',),f'Wrong [K:{uuid4()}]')])):
            service=IndustrialQaService(SPEC,knowledge,documents=documents,
                pipeline=IndustrialQaPipeline(retriever=build_retriever(ranker=object()),writer=IndustrialAnswerWriter(object())))
            events=[]
            with self.assertRaises(InvalidKnowledgeCitation):
                async for e in service.start_stream('encoder',thread_id='a').events:
                    events.append(e)
        self.assertFalse(any(e.event.startswith('answer.') for e in events))
        self.assertEqual(service._threads['a'].history,[])

    async def test_timeout_and_storage_errors_use_existing_service_contract(self):
        for failure,expected in ((StorageError('unavailable'),KnowledgeAgentUnavailable),
                                 (llm.LLMResponseError('bad JSON'),KnowledgeAgentUpstreamError)):
            knowledge,documents=stores()
            pipeline=SimpleNamespace(retrieve=AsyncMock(side_effect=failure))
            service=IndustrialQaService(SPEC,knowledge,documents=documents,pipeline=pipeline)
            with self.assertRaises(expected):
                await service.ask('encoder')
            self.assertEqual(service._threads,{})
        knowledge,documents=stores()
        async def never(*_args,**_kwargs):
            await asyncio.Event().wait()
        service=IndustrialQaService(SPEC,knowledge,documents=documents,
            pipeline=SimpleNamespace(retrieve=never),timeout_seconds=0.01)
        with self.assertRaises(KnowledgeAgentUpstreamError):
            await service.ask('encoder')

    async def test_owned_document_store_is_opened_once_and_closed(self):
        knowledge,documents=stores()
        result=RetrievalResult(())
        pipeline=SimpleNamespace(retrieve=AsyncMock(return_value=result),
            write=AsyncMock(return_value=(WrittenAnswer.model_validate_json(written((), 'No evidence.')),None)))
        with patch('backend.knowledge_bases.industrial_qa.service.open_document_store',AsyncMock(return_value=documents)) as opened:
            service=IndustrialQaService(SPEC,knowledge,pipeline=pipeline)
            await service.ask('encoder')
            await service.ask('encoder')
            opened.assert_awaited_once()
            self.assertFalse(opened.call_args.kwargs['init_schema'])
            await service.aclose()
        documents.aclose.assert_awaited_once()
        knowledge.aclose.assert_not_awaited()

    async def test_same_thread_serializes_turns_and_cancellation_releases_it(self):
        knowledge, documents = stores()
        entered, release = asyncio.Event(), asyncio.Event()
        queries = []
        histories = []
        async def retrieve(question, *_args, **_kwargs):
            queries.append(question)
            if question == 'first':
                entered.set()
                await release.wait()
            return RetrievalResult(())
        async def write(question, _retrieved, *, history):
            histories.append(history)
            return WrittenAnswer.model_validate_json(written((), question)), None
        service = IndustrialQaService(SPEC, knowledge, documents=documents,
            pipeline=SimpleNamespace(retrieve=retrieve,write=write))
        async def collect(query):
            return [e async for e in service.start_stream(query,thread_id='a').events]
        first=asyncio.create_task(collect('first'))
        await entered.wait()
        second=asyncio.create_task(collect('second'))
        await asyncio.sleep(0)
        self.assertEqual(queries,['first'])
        release.set()
        await asyncio.gather(first,second)
        self.assertIn('User: first',histories[1])
        await service.delete_thread('a')
        entered.clear()
        release.clear()
        cancelled=asyncio.create_task(collect('first'))
        await entered.wait()
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        await service.delete_thread('a')
        self.assertNotIn('a',service._threads)


class IndustrialHttpTests(unittest.TestCase):
    def test_case_presentation_metadata_survives_public_sse_without_internal_context(self):
        card = {"context": "Line 5", "observation": "Alarm", "reasoning": "Suspected welder",
                "action": "Planned checks", "outcome": None,
                "evidence": [{"turn_id": "T03", "quote": "Check first."}]}
        rec = record(machine_id="MR-JE-AX205", case_card=card, benchmark_run_id="private-run")
        rec.type = KnowledgeType.CASE
        knowledge, documents = stores([rec])
        pipeline = SimpleNamespace(retrieve=AsyncMock(return_value=RetrievalResult((Source("K1", record=rec),))),
            write=AsyncMock(return_value=(WrittenAnswer.model_validate_json(written(("K1",))), None)))
        service = IndustrialQaService(SPEC, knowledge, documents=documents, pipeline=pipeline)
        with TestClient(create_app(knowledge_agents={"industrial_qa": service})) as client:
            response = client.post("/v1/knowledge-agent/industrial_qa/stream",
                json={"query": "MR-JE-AX205", "thread_id": "case-ui"})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        (citation,) = next(event["data"]["citations"] for event in events if event["type"] == "answer.citations")
        self.assertEqual(citation["case_card"], card)
        self.assertEqual(citation["status"], "draft")
        self.assertEqual(citation["scope"], {"machine_id": "MR-JE-AX205"})
        self.assertNotIn("private-run", response.text)

    def test_registered_factory_pipeline_runs_via_public_http_and_closes_stores(self):
        rec,chunk=record(machine_id='MR-JE-AX391',machine_model='MR-JE-70A'),passage()
        knowledge,documents=stores([rec],[chunk])
        with patch.object(llm,'chat_client_from_env',return_value=object()), \
                patch.object(llm,'ainvoke',AsyncMock(side_effect=['{"labels":["K1","D1"]}',written()])), \
                patch('backend.api.knowledge_agent.open_store',AsyncMock(return_value=knowledge)), \
                patch('backend.knowledge_bases.industrial_qa.service.open_document_store',AsyncMock(return_value=documents)):
            with TestClient(create_app()) as client:
                response=client.post('/v1/knowledge-agent/industrial_qa/stream',
                    json={'query':'MR-JE-AX391 connectors','thread_id':'a'})
                self.assertEqual(response.status_code,200)
                self.assertIn('event: run.completed',response.text)
                self.assertIn(f'[K:{rec.id}]',response.text)
                self.assertIn(f'[D:{chunk.doc_id}]',response.text)
        knowledge.aclose.assert_awaited_once()
        documents.aclose.assert_awaited_once()


if __name__=='__main__':
    unittest.main()
