import pandas as pd
import numpy as np
from langchain_huggingface.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaLLM
from langchain.text_splitter import RecursiveCharacterTextSplitter
from sklearn.cluster import KMeans
import json
import time
from typing import List, Dict
import logging

# from transformers import PreTrainedModel

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Simulated Hyperliquid whale position data (replace with real API data in production)
# Sample format: wallet, asset, position_type, notional_value, leverage, entry_price, liquidation_price, unrealized_pnl
whale_positions = [
    {"wallet": "JamesWynn", "asset": "BTC", "position_type": "long", "notional_value": 830_000_000, "leverage": 40, "entry_price": 107_000, "liquidation_price": 105_500, "unrealized_pnl": 39_000_000},
    {"wallet": "0x20c2..f5", "asset": "ETH", "position_type": "short", "notional_value": 1_117_000_000, "leverage": 25, "entry_price": 2_241, "liquidation_price": 2_300, "unrealized_pnl": 17_500_000},
    # ... Simulated 298 more positions (abridged for brevity)
] * 150  # Simulate 300 positions by repeating and modifying

# Simulated market trend data (30-day price/volume for BTC, ETH, SOL)
market_trends = {
    "BTC": {"price_start": 97_000, "price_end": 107_000, "volume": 4.9e9, "trend": "bullish"},
    "ETH": {"price_start": 2_100, "price_end": 2_241, "volume": 2.1e9, "trend": "neutral"},
    "SOL": {"price_start": 150, "price_end": 160, "volume": 1.2e9, "trend": "neutral"},
}

# Initialize LLM and embeddings (open-source for local processing)
# llm = HuggingFacePipeline.from_model_id(
#     model_id="meta-llama/Meta-Llama-3-8B",  # Replace with Grok 3 or stronger model in production
#     task="text-generation",
#     pipeline_kwargs={"max_new_tokens": 500}
# )

llm = OllamaLLM(model="llama3.1", base_url="http://localhost:11434")

embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")

# Step 1: Data Preprocessing and Tree Construction
def preprocess_data(positions: List[Dict], trends: Dict) -> List[str]:
    """Convert position and trend data into text documents for RAPTOR."""
    documents = []
    for pos in positions:
        doc = f"Wallet: {pos['wallet']}, Asset: {pos['asset']}, Position: {pos['position_type']}, " \
              f"Notional: ${pos['notional_value']:,.0f}, Leverage: {pos['leverage']}x, " \
              f"Entry: ${pos['entry_price']:,.2f}, Liquidation: ${pos['liquidation_price']:,.2f}, " \
              f"PNL: ${pos['unrealized_pnl']:,.0f}"
        documents.append(doc)
    for asset, trend in trends.items():
        doc = f"Asset: {asset}, 30-day Start Price: ${trend['price_start']:,.2f}, " \
              f"End Price: ${trend['price_end']:,.2f}, Volume: ${trend['volume']:,.0f}, Trend: {trend['trend']}"
        documents.append(doc)
    return documents

def build_raptor_tree(documents: List[str], max_depth: int = 3) -> Dict:
    """Build RAPTOR hierarchical tree with clustering and summarization."""
    # Split documents into chunks
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = text_splitter.split_text("\n".join(documents))
    
    # Embed chunks
    embeddings_array = embeddings.embed_documents(chunks)
    
    # Cluster chunks using KMeans
    n_clusters = min(10, len(chunks))  # Adjust based on data size
    kmeans = KMeans(n_clusters=n_clusters, random_state=42)
    cluster_labels = kmeans.fit_predict(embeddings_array)
    
    # Summarize clusters
    tree = {"level_0": []}
    for cluster_id in range(n_clusters):
        cluster_chunks = [chunks[i] for i in range(len(chunks)) if cluster_labels[i] == cluster_id]
        cluster_text = "\n".join(cluster_chunks)
        summary_prompt = f"Summarize the following whale position and market trend data concisely:\n{cluster_text}"
        summary = llm(summary_prompt).strip()
        tree["level_0"].append({"cluster_id": cluster_id, "summary": summary, "chunks": cluster_chunks})
    
    # Recursive summarization (simplified for sample, extend to max_depth in production)
    tree["level_1"] = []
    level_1_text = "\n".join([node["summary"] for node in tree["level_0"]])
    summary_prompt = f"Summarize the following cluster summaries into a high-level overview:\n{level_1_text}"
    tree["level_1"].append({"summary": llm(summary_prompt).strip()})
    
    # Store embeddings in FAISS
    vector_store = FAISS.from_texts(chunks, embeddings)
    return tree, vector_store

# Step 2: Query RAPTOR Tree
def query_raptor_tree(query: str, tree: Dict, vector_store: FAISS, k: int = 5) -> List[str]:
    """Query the RAPTOR tree for relevant information."""
    # Search vector store for relevant chunks
    results = vector_store.similarity_search(query, k=k)
    retrieved_chunks = [res.page_content for res in results]
    
    # Include high-level summaries from tree
    summaries = [node["summary"] for node in tree["level_0"]] + [tree["level_1"][0]["summary"]]
    return summaries + retrieved_chunks

# Step 3: Execute Task Steps
def analyze_whale_positions():
    # Preprocess data
    logger.info("Preprocessing whale positions and market trends...")
    documents = preprocess_data(whale_positions, market_trends)
    
    # Build RAPTOR tree
    logger.info("Building RAPTOR tree...")
    tree, vector_store = build_raptor_tree(documents)
    
    # Step 2: Select top 10 whales
    query = "Identify whales with PNL > $1M and 1–3 assets traded"
    results = query_raptor_tree(query, tree, vector_store)
    top_whales = []
    for result in results:
        if "Wallet" in result and "PNL" in result:
            top_whales.append(result)
            if len(top_whales) >= 10:
                break
    logger.info(f"Selected top whales: {top_whales[:2]}...")  # Log first 2 for brevity
    
    # Step 3: Identify trading patterns
    query = "Analyze recent whale trades and historical PNL for trading patterns"
    patterns = query_raptor_tree(query, tree, vector_store)
    patterns_summary = llm(f"Summarize trading patterns from:\n{'\n'.join(patterns)}").strip()
    logger.info(f"Trading patterns: {patterns_summary}")
    
    # Step 4: Risk assessment
    query = "Assess risks of positions with >20x leverage and liquidation margins <2%"
    risks = query_raptor_tree(query, tree, vector_store)
    risk_summary = llm(f"Summarize risks from:\n{'\n'.join(risks)}").strip()
    logger.info(f"Risk assessment: {risk_summary}")
    
    # Step 5: Market trends
    query = "Summarize 30-day price and volume trends for BTC, ETH, SOL"
    trends = query_raptor_tree(query, tree, vector_store)
    trend_summary = llm(f"Summarize market trends from:\n{'\n'.join(trends)}").strip()
    logger.info(f"Market trends: {trend_summary}")
    
    # Step 6: Propose trades
    query = "Propose short-term trades based on whale patterns and market trends"
    trade_data = query_raptor_tree(query, tree, vector_store)
    trade_prompt = f"Based on:\n{'\n'.join(trade_data)}\nPropose 4 short-term trades with entry, stop loss, take profit, leverage, and safety score."
    trades = llm(trade_prompt).strip()
    logger.info(f"Proposed trades:\n{trades}")
    
    return {
        "top_whales": top_whales,
        "patterns": patterns_summary,
        "risks": risk_summary,
        "trends": trend_summary,
        "trades": trades
    }

# Run analysis
if __name__ == "__main__":
    start_time = time.time()
    results = analyze_whale_positions()
    logger.info(f"Analysis completed in {time.time() - start_time:.2f} seconds")
    
    # Print results
    print("Top Whales:", results["top_whales"][:2], "...")
    print("Trading Patterns:", results["patterns"])
    print("Risk Assessment:", results["risks"])
    print("Market Trends:", results["trends"])
    print("Proposed Trades:", results["trades"])