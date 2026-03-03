"""
Centralized LLM configuration for the agent engine.
"""

import os
from dotenv import load_dotenv
from pydantic_ai.models.google import GoogleModel

load_dotenv()

# Validate API key is present
if not os.getenv("GEMINI_API_KEY"):
    raise ValueError("GEMINI_API_KEY environment variable not set")

# Shared model instance for all agents
reasoning_model = GoogleModel('gemini-2.5-pro')
