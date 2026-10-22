"""Official industrial QA pipeline: scoped retrieval, reranking, then answer."""
from backend.knowledge_bases.industrial_qa.answering import IndustrialAnswerWriter
from backend.knowledge_bases.industrial_qa.config import load_config
from backend.knowledge_bases.industrial_qa.query import QueryRewriter, RewrittenQuery
from backend.knowledge_bases.industrial_qa.retrieval import IndustrialRetriever, Ranking


def build_rewriter(config=None, *, client=None):
    """R2 query rewriter, or None when [retrieval] query_rewrite is off."""
    config = config if config is not None else load_config()
    if config.retrieval.query_rewrite == "off":
        return None
    client = client if client is not None else config.query.client("query_rewrite", RewrittenQuery)
    return QueryRewriter(client, config.prompts["query"], env_prefix=config.query.env_prefix)


def build_retriever(config=None, *, ranker=None, prefilter=None, rewriter=None):
    config = config if config is not None else load_config()
    ranker = ranker if ranker is not None else config.rerank.client("source_ranking", Ranking)
    rewriter = rewriter if rewriter is not None else build_rewriter(config)
    return IndustrialRetriever(ranker, config=config.retrieval, prefilter=prefilter,
        prompt=config.prompts["rerank"], env_prefix=config.rerank.env_prefix, rewriter=rewriter)


class IndustrialQaPipeline:
    def __init__(self, *, config=None, retriever=None, writer=None):
        self.config = config if config is not None else load_config()
        self.retriever = retriever if retriever is not None else build_retriever(self.config)
        self.writer = writer if writer is not None else IndustrialAnswerWriter(config=self.config)

    async def retrieve(self, question, knowledge, documents, **kwargs):
        """`history` (prior question/answer turns) lets a follow-up inherit scope and steer R2."""
        return await self.retriever.retrieve(question, knowledge, documents, **kwargs)

    async def write(self, question, retrieval, *, history=""):
        return await self.writer.write(question, {s.label: s.text for s in retrieval.sources}, history=history)
