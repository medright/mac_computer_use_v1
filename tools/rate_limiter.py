import asyncio
import json
import os
from pathlib import Path
import streamlit as st
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, Optional
from streamlit.runtime.scriptrunner import add_script_run_ctx

@dataclass
class RateLimit:
    requests_per_minute: int
    tokens_per_minute: int 
    tokens_per_day: int

@dataclass
class Usage:
    request_count: int = 0
    token_count: int = 0
    last_reset: datetime = datetime.now()
    daily_tokens: int = 0
    daily_reset: datetime = datetime.now()

class RateLimiter:
    """Tracks and enforces API rate limits"""
    
    # Rate limits for different Claude models
    LIMITS = {
        "claude-3-opus": RateLimit(50, 20_000, 1_000_000),
        "claude-3-sonnet": RateLimit(50, 40_000, 1_000_000),
        "claude-3-haiku": RateLimit(50, 50_000, 5_000_000),
        "claude-3-5-sonnet-20241022": RateLimit(50, 40_000, 1_000_000),
        "claude-3-5-sonnet-20240620": RateLimit(50, 40_000, 1_000_000),
    }

    def __init__(self):
        self.usage: Dict[str, Usage] = {}
        self.data_file = Path.home() / '.anthropic' / 'token_usage.json'
        self.load_usage()
        
    def check_limits(self, model: str, token_count: int) -> Optional[str]:
        """
        Check if the request would exceed rate limits.
        Returns error message if limits would be exceeded, None otherwise.
        """
        now = datetime.now()
        
        # Get or create usage tracking for this model
        if model not in self.usage:
            self.usage[model] = Usage()
        usage = self.usage[model]
        limits = self.LIMITS.get(model)
        
        if not limits:
            return None # No limits defined for this model
            
        # Reset minute counters if minute has elapsed
        time_since_reset = now - usage.last_reset
        if time_since_reset >= timedelta(minutes=1):
            print(f"Resetting minute counters after {time_since_reset.total_seconds()}s")
            usage.request_count = 0
            usage.token_count = 0
            usage.last_reset = now
            
        # Reset daily counter if day has elapsed
        days_since_reset = (now - usage.daily_reset).days
        if days_since_reset >= 1:
            print(f"Resetting daily counter after {days_since_reset} days")
            usage.daily_tokens = 0
            usage.daily_reset = now
            
        # Check if this request would exceed limits
        next_request_count = usage.request_count + 1
        next_token_count = usage.token_count + token_count
        next_daily_tokens = usage.daily_tokens + token_count
        
        print(f"""
Rate limit check:
- Requests: {next_request_count}/{limits.requests_per_minute} per minute
- Tokens: {next_token_count}/{limits.tokens_per_minute} per minute
- Daily Tokens: {next_daily_tokens}/{limits.tokens_per_day}
        """)
        
        if next_request_count > limits.requests_per_minute:
            return f"Request would exceed rate limit of {limits.requests_per_minute} requests per minute"
            
        if next_token_count > limits.tokens_per_minute:
            return f"Request would exceed rate limit of {limits.tokens_per_minute} tokens per minute"
            
        if next_daily_tokens > limits.tokens_per_day:
            return f"Request would exceed rate limit of {limits.tokens_per_day} tokens per day"
            
        return None
        
    def record_usage(self, model: str, token_count: int):
        """Record the usage of tokens and increment request counter"""
        if model not in self.usage:
            self.usage[model] = Usage()
        
        usage = self.usage[model]
        usage.request_count += 1
        usage.token_count += token_count
        usage.daily_tokens += token_count
        
        # Save the updated usage
        self.save_usage()
        
    def load_usage(self):
        """Load persisted usage data from file"""
        try:
            if self.data_file.exists():
                data = json.loads(self.data_file.read_text())
                for model, usage_data in data.items():
                    usage = Usage()
                    usage.daily_tokens = usage_data.get('daily_tokens', 0)
                    usage.daily_reset = datetime.fromisoformat(usage_data.get('daily_reset', datetime.now().isoformat()))
                    self.usage[model] = usage
        except Exception as e:
            print(f"Error loading usage data: {e}")

    def save_usage(self):
        """Save usage data to file"""
        try:
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for model, usage in self.usage.items():
                data[model] = {
                    'daily_tokens': usage.daily_tokens,
                    'daily_reset': usage.daily_reset.isoformat()
                }
            self.data_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"Error saving usage data: {e}")

    def get_usage_stats(self, model: str) -> dict:
        """Get current usage statistics for the model"""
        if model not in self.usage:
            return {}
            
        usage = self.usage[model]
        limits = self.LIMITS.get(model)
        
        if not limits:
            return {}
            
        return {
            "requests_per_minute": {
                "current": usage.request_count,
                "limit": limits.requests_per_minute,
                "remaining": limits.requests_per_minute - usage.request_count
            },
            "tokens_per_minute": {
                "current": usage.token_count,
                "limit": limits.tokens_per_minute,
                "remaining": limits.tokens_per_minute - usage.token_count
            },
            "tokens_per_day": {
                "current": usage.daily_tokens,
                "limit": limits.tokens_per_day,
                "remaining": limits.tokens_per_day - usage.daily_tokens
            }
        }

    async def wait_if_needed(self, model: str, token_count: int):
        """Wait until rate limits allow the request and show status in Streamlit UI"""
        # Add Streamlit context to this async function
        add_script_run_ctx()
        
        while True:
            error = self.check_limits(model, token_count)
            if not error:
                break
                
            # Get current usage stats
            stats = self.get_usage_stats(model)
            
            # Create a detailed message
            if stats:
                message = f"""
Rate limit reached: {error}
Current Usage:
- Requests: {stats['requests_per_minute']['current']}/{stats['requests_per_minute']['limit']} per minute
- Tokens: {stats['tokens_per_minute']['current']}/{stats['tokens_per_minute']['limit']} per minute
- Daily Tokens: {stats['tokens_per_day']['current']}/{stats['tokens_per_day']['limit']}
                """
            else:
                message = f"Rate limit reached: {error}"
                
            # Show warning message in Streamlit
            st.warning(message, icon="⏳")
            await asyncio.sleep(15)
