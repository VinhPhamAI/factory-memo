"""Runtime/benchmark parity, evidence boundaries, and industrial SSE behavior."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.knowledge_bases import AnswerPipeline, get_kb
from backend.knowledge_bases.industrial_qa import SPEC
from backend.knowledge_bases.industrial_qa.answering import IndustrialAnswerWriter, WrittenAnswer
from backend.knowledge_bases.industrial_qa.benchmark_adapter import ScopedRetrieval
from backend.knowledge_bases.industrial_qa.pipeline import IndustrialQaPipeline, build_retriever
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
            core = build_retriever(ranker=object(), prefilter=prefilter,
                config=RetrievalConfig(prefilter_knowledge=2, prefilter_manual=1))
            result = await core.retrieve('encoder', knowledge, documents)
            await ScopedRetrieval(scope=None, retriever=core)('encoder', knowledge, documents,
                {'run_id': 'run'}, SimpleNamespace(k=8, per_conversation=3), {}, {}, {})
        # The cross-encoder never sees the ephemeral labels it reorders.
        self.assertFalse(any(text.startswith('[') for text in prefilter.score.call_args.args[1]))
        self.assertEqual((result.pool, result.candidates), (6, 3))
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
            core = build_retriever(ranker=object(),
                config=RetrievalConfig(rerank_mode='hybrid', prefilter_knowledge=2, prefilter_manual=1))
            result = await core.retrieve('encoder', knowledge, documents)
            await ScopedRetrieval(scope=None, retriever=core)('encoder', knowledge, documents,
                {'run_id': 'run'}, SimpleNamespace(k=8, per_conversation=3), {}, {}, {})
        self.assertEqual((result.pool, result.candidates), (4, 4))
        self.assertEqual([s.text.splitlines()[-1] for s in result.sources],
                         ['claim 1', 'procedure step 2', 'Connection procedure 0'])
        for prompt in (call.args[1] for call in invoke.call_args_list):
            self.assertNotIn('claim 2', prompt)
            self.assertNotIn('manual 1', prompt)
            self.assertIn('procedure step 2', prompt)
        with self.assertRaises(ValueError):
            RetrievalConfig(rerank_mode='cross_encoder')

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
        async def retrieve(question, *_args):
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
