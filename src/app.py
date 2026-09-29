from fastapi import FastAPI, HTTPException, Body, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
from backend.core.src.agents.llm.openai_llm import OpenAI_LLM
from backend.utils.schema import Message
from pathlib import Path
import json
from json_repair import repair_json
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from backend.core.src.agents.mcp_servers import load_mcp_servers

from langchain.schema import HumanMessage
from langchain.callbacks import AsyncIteratorCallbackHandler
from typing import AsyncIterable
from langchain_openai import ChatOpenAI
import asyncio, os
from backend.utils.helpers import _make_event

load_dotenv()

app = FastAPI(title="Voice Agent Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    content: str

class AgentChatRequest(BaseModel):
    message: str
    user_id: str = "user1"
    agent_id: str = "AI Assistant"
    agent_info: str = "General knowledge and helpful information"
    llm_name: str = "Qwen/Qwen2.5-72B-Instruct-Turbo"
    planning_type: str = "ReAct"
    mcp_server_urls: Dict[str, str] = {}

@app.post("/chat_simple_llm", response_model=ChatResponse)
async def chat_simple_llm(request: ChatRequest = Body(...)):
    """
    Generate text using LiteLLM without tool calls.
    
    Args:
        request: ChatRequest containing message
        
    Returns:
        ChatResponse with generated content
    """
    try:
        # Configure LLM
        llm_config = {
            "model_name": "Qwen/Qwen2.5-7B-Instruct-Turbo",
            "temperature": 0.8,
            "max_tokens": 2048,
            "api_base": "",
        }
            
        # Initialize LiteLLM
        llm = LiteLLM(llm_config)
        
        # Create a simple user message from the input string
        messages = [Message.user_message(request.message)]
        
        # Generate completion
        response = await llm.chat_completion(
            messages=messages
        )
        
        # Extract content from response
        content = response.choices[0].message.content
        
        # Return formatted response
        return ChatResponse(
            content=content,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating text: {str(e)}")

@app.post("/chat_with_agent", response_model=ChatResponse)
async def agent_chat(request: AgentChatRequest = Body(...)):
    """
    Generate a response using the Agent with memory and planning capabilities.
    
    Args:
        request: AgentChatRequest containing message and agent configuration
        
    Returns:
        ChatResponse with generated content
    """
    try:
        # Configure MCP servers with provided URLs or use defaults
        mcp_servers = {}
        
        # Default server names if not provided
        default_servers = await load_mcp_servers()
        print("Default servers:", default_servers)
        # If user provided server URLs, use those
        if request.mcp_server_urls:
            for server_name, url in request.mcp_server_urls.items():
                mcp_servers[server_name] = {
                    "url": url,
                    "transport": "sse",
                }
        else:
            # Otherwise use defaults
            for server_name, url in default_servers.items():
                mcp_servers[server_name] = {
                    "url": url,
                    "transport": "sse",
                }
        
        # Get memory config from environment
        memory_config = os.getenv("MEM0_API_KEY")
        
        # Create agent with the provided configuration
        agent = Agent(
            user_id=request.user_id,
            agent_id=request.agent_id,
            llm_name=request.llm_name,
            agent_info=request.agent_info,
            planning_type=request.planning_type,
            mcp_servers=mcp_servers,
            memory_config=memory_config
        )
        
        # Initialize the agent
        await agent.initialize()
        
        # Process the input and get response
        response = await agent.chat(request.message)
        
        # Clean up resources
        await agent.close()
        
        # Return formatted response
        return ChatResponse(
            content=response,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating agent response: {str(e)}")

@app.post("/chat_with_agent/stream")
async def agent_chat_stream(request: AgentChatRequest = Body(...)):
    """
    Generate a streaming response using the Agent with memory and planning capabilities.
    
    Args:
        request: AgentChatRequest containing message and agent configuration
        
    Returns:
        StreamingResponse with generated content
    """
    try:
        # Configure MCP servers with provided URLs or use defaults
        mcp_servers = {}
        
        # Default server names if not provided
        default_servers = await load_mcp_servers()
        
        # If user provided server URLs, use those
        if request.mcp_server_urls:
            for server_name, url in request.mcp_server_urls.items():
                mcp_servers[server_name] = {
                    "url": url,
                    "transport": "sse",
                }
        else:
            # Otherwise use defaults
            for server_name, url in default_servers.items():
                mcp_servers[server_name] = {
                    "url": url,
                    "transport": "sse",
                }
        
        # Get memory config from environment
        memory_config = os.getenv("MEM0_API_KEY")
        
        # Create agent with the provided configuration
        agent = Agent(
            user_id=request.user_id,
            agent_id=request.agent_id,
            llm_name=request.llm_name,
            agent_info=request.agent_info,
            planning_type=request.planning_type,
            mcp_servers=mcp_servers,
            memory_config=memory_config
        )
        
        # Initialize the agent
        await agent.initialize()
        
        # Return streaming response
        return StreamingResponse(
            agent.chat_streaming(request.message),
            media_type="text/event-stream"
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating agent response: {str(e)}")

# result = asyncio.run(agent_bootstrapping(request=AgentBootstrapping(agent_id="Agent 2")))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)