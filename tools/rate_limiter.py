import asyncio
import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional
import time
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx
import logging

# Configure logging at the top of the file
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

@dataclass
class RateLimit:
    requests_per_minute: int
    tokens_per_minute: int 
    tokens_per_day: int

@dataclass
class ModelLimits:
    opus: RateLimit
    sonnet: RateLimit
    haiku: RateLimit

@dataclass
class UsageCounter:
    current: int = 0
    timestamp: float = field(default_factory=time.time)

@dataclass
class TokenCounter:
    input: UsageCounter = field(default_factory=UsageCounter)
    output: UsageCounter = field(default_factory=UsageCounter)

@dataclass
class Usage:
    requests_per_minute: UsageCounter = field(default_factory=UsageCounter)
    tokens_per_minute: TokenCounter = field(default_factory=TokenCounter)
    tokens_per_day: TokenCounter = field(default_factory=TokenCounter)

class RateLimiter:
    """Tracks and enforces API rate limits"""
    
    # Define limits for each tier
    TIER_LIMITS = {
        1: ModelLimits(
            opus=RateLimit(50, 20_000, 1_000_000),
            sonnet=RateLimit(50, 40_000, 1_000_000),
            haiku=RateLimit(50, 50_000, 5_000_000)
        ),
        2: ModelLimits(
            opus=RateLimit(1000, 40_000, 2_500_000),
            sonnet=RateLimit(1000, 80_000, 2_500_000),
            haiku=RateLimit(1000, 100_000, 25_000_000)
        ),
        3: ModelLimits(
            opus=RateLimit(2000, 80_000, 5_000_000),
            sonnet=RateLimit(2000, 160_000, 5_000_000),
            haiku=RateLimit(2000, 200_000, 50_000_000)
        ),
        4: ModelLimits(  # You can add Tier 4 limits when available
            opus=RateLimit(5000, 160_000, 10_000_000),    # Example values
            sonnet=RateLimit(5000, 320_000, 10_000_000),  # Example values
            haiku=RateLimit(5000, 400_000, 100_000_000)   # Example values
        )
    }
    
    # Test limits (for testing rate limit behavior) - set to ~5% of Tier 1
    TEST_LIMITS = ModelLimits(
        opus=RateLimit(5, 1_000, 50_000),
        sonnet=RateLimit(5, 2_000, 50_000),
        haiku=RateLimit(5, 2_500, 250_000)
    )

    def __init__(self):
        self.usage: Dict[str, Usage] = {}
        self.data_file = Path.home() / '.anthropic' / 'token_usage.json'
        self.load_usage()
        
        # Store current tier
        self.current_tier = int(os.getenv('ANTHROPIC_TIER', '1'))
        
        # Determine which limits to use
        self.test_mode = os.getenv('RATE_LIMIT_TEST_MODE', '').lower() == 'true'
        
        if self.test_mode:
            self.current_limits = self.TEST_LIMITS
        else:
            self.current_limits = self.TIER_LIMITS[self.current_tier]
            
        # Map model names to their limit types
        self.MODEL_LIMIT_MAP = {
            'claude-3-opus': 'opus',
            'claude-3-sonnet': 'sonnet',
            'claude-3-haiku': 'haiku',
            'claude-3-5-sonnet-20241022': 'sonnet',
            'claude-3-5-sonnet-20240620': 'sonnet'
        }

    def _get_model_type(self, model: str) -> str:
        """Determine model type from model name"""
        model_lower = model.lower()
        if 'opus' in model_lower:
            return 'opus'
        elif 'haiku' in model_lower:
            return 'haiku'
        else:
            return 'sonnet'  # Default to sonnet for unknown models

    def _get_model_limits(self, model: str) -> Dict[str, int]:
        """Get the rate limits for a specific model"""
        model_type = self._get_model_type(model)
        limits = getattr(self.current_limits, model_type)
        
        return {
            'requests_per_minute': limits.requests_per_minute,
            'tokens_per_minute': limits.tokens_per_minute,
            'tokens_per_day': limits.tokens_per_day
        }

    def get_tier_info(self) -> str:
        """Get information about current tier and limits"""
        if self.test_mode:
            return "Test Mode (Limited Rate)"
            
        tier_names = {
            1: "Tier 1 (Starter)",
            2: "Tier 2 (Scale)",
            3: "Tier 3 (Growth)",
            4: "Tier 4 (Enterprise)"
        }
        
        model_type = next(iter(self.MODEL_LIMIT_MAP.values()))  # Get first model type
        limits = getattr(self.current_limits, model_type)
        
        return f"{tier_names.get(self.current_tier, f'Tier {self.current_tier}')} - {limits.tokens_per_day:,} TPD"

    def get_tier_limits(self) -> str:
        """Get detailed information about current tier limits"""
        if self.test_mode:
            return "Test Mode - Limited Rates"
            
        model = st.session_state.get('model', '')
        model_type = self._get_model_type(model)
        limits = getattr(self.current_limits, model_type)
        
        return f"""
Current Tier {self.current_tier} Limits for {model_type.title()} models:
- Requests per minute: {limits.requests_per_minute:,}
- Tokens per minute: {limits.tokens_per_minute:,}
- Tokens per day: {limits.tokens_per_day:,}
"""

    def check_limits(self, model: str, token_count: int) -> Optional[str]:
        """
        Check if the request would exceed rate limits.
        Returns error message if limits would be exceeded, None otherwise.
        """
        # Skip limit checking if DISABLE_RATE_LIMITS is set
        if os.getenv('DISABLE_RATE_LIMITS', '').lower() == 'true':
            return None
            
        if model not in self.usage:
            self.usage[model] = Usage()
            
        usage = self.usage[model]
        limits = self._get_model_limits(model)
        
        if not limits:
            return None
            
        now = time.time()
        
        # Reset per-minute counters if more than 60 seconds have passed
        if now - usage.requests_per_minute.timestamp >= 60:
            logger.debug("Resetting per-minute counters due to time window expiration")
            usage.requests_per_minute.current = 0
            usage.requests_per_minute.timestamp = now
            usage.tokens_per_minute.input.current = 0
            usage.tokens_per_minute.input.timestamp = now
            usage.tokens_per_minute.output.current = 0
            usage.tokens_per_minute.output.timestamp = now
            
        # Reset daily counters if more than 24 hours have passed
        if now - usage.tokens_per_day.input.timestamp >= 86400:
            logger.debug("Resetting daily counters due to time window expiration")
            usage.tokens_per_day.input.current = 0
            usage.tokens_per_day.input.timestamp = now
            usage.tokens_per_day.output.current = 0
            usage.tokens_per_day.output.timestamp = now
        
        # Log current usage
        logger.debug(f"Checking limits for model: {model}")
        logger.debug(f"Requests per minute: {usage.requests_per_minute.current}/{limits['requests_per_minute']}")
        logger.debug(f"Tokens per minute: {usage.tokens_per_minute.input.current + usage.tokens_per_minute.output.current}/{limits['tokens_per_minute']}")
        logger.debug(f"Tokens per day: {usage.tokens_per_day.input.current + usage.tokens_per_day.output.current}/{limits['tokens_per_day']}")
        
        # Check limits
        if usage.requests_per_minute.current >= limits['requests_per_minute']:
            logger.warning("Exceeded requests per minute limit.")
            return f"Request would exceed rate limit of {limits['requests_per_minute']} requests per minute"
            
        total_tokens_per_minute = usage.tokens_per_minute.input.current + usage.tokens_per_minute.output.current
        if total_tokens_per_minute >= limits['tokens_per_minute']:
            logger.warning("Exceeded tokens per minute limit.")
            return f"Request would exceed rate limit of {limits['tokens_per_minute']} tokens per minute"
            
        total_tokens_per_day = usage.tokens_per_day.input.current + usage.tokens_per_day.output.current
        if total_tokens_per_day >= limits['tokens_per_day']:
            logger.warning("Exceeded tokens per day limit.")
            return f"Request would exceed rate limit of {limits['tokens_per_day']} tokens per day"
            
        return None
        
    def record_usage(self, model: str, input_tokens: int, output_tokens: int):
        """Record API usage for rate limiting"""
        if model not in self.usage:
            self.usage[model] = Usage()

        # Update request count
        self._update_counter(self.usage[model].requests_per_minute, 1, 60)
        
        # Update token counts
        self._update_token_counters(self.usage[model].tokens_per_minute, input_tokens, output_tokens, 60)
        self._update_token_counters(self.usage[model].tokens_per_day, input_tokens, output_tokens, 86400)

    def get_usage_stats(self, model: str):
        """Get current usage statistics"""
        if model not in self.usage:
            return None

        usage = self.usage[model]
        limits = self._get_model_limits(model)

        return {
            'requests_per_minute': {
                'current': usage.requests_per_minute.current,
                'limit': limits['requests_per_minute'],
                'remaining': limits['requests_per_minute'] - usage.requests_per_minute.current
            },
            'tokens_per_minute': {
                'current': usage.tokens_per_minute.input.current + usage.tokens_per_minute.output.current,
                'input': usage.tokens_per_minute.input.current,
                'output': usage.tokens_per_minute.output.current,
                'limit': limits['tokens_per_minute'],
                'remaining': limits['tokens_per_minute'] - (usage.tokens_per_minute.input.current + usage.tokens_per_minute.output.current)
            },
            'tokens_per_day': {
                'current': usage.tokens_per_day.input.current + usage.tokens_per_day.output.current,
                'input': usage.tokens_per_day.input.current,
                'output': usage.tokens_per_day.output.current,
                'limit': limits['tokens_per_day']
            }
        }

    def _update_counter(self, counter: UsageCounter, value: int, window_seconds: int):
        """Update a simple counter"""
        now = time.time()
        if now - counter.timestamp > window_seconds:
            logger.debug(f"Resetting counter. Previous value: {counter.current}, Resetting to 0.")
            counter.current = 0
            counter.timestamp = now
        counter.current += value
        logger.debug(f"Counter updated. New value: {counter.current}")

    def _update_token_counters(self, counter: TokenCounter, input_tokens: int, output_tokens: int, window_seconds: int):
        """Update both input and output token counters"""
        now = time.time()
        # Reset input counter
        if now - counter.input.timestamp > window_seconds:
            logger.debug(f"Resetting input token counter. Previous value: {counter.input.current}, Resetting to 0.")
            counter.input.current = 0
            counter.input.timestamp = now
        # Reset output counter
        if now - counter.output.timestamp > window_seconds:
            logger.debug(f"Resetting output token counter. Previous value: {counter.output.current}, Resetting to 0.")
            counter.output.current = 0
            counter.output.timestamp = now
        
        # Update counters
        counter.input.current += input_tokens
        counter.output.current += output_tokens
        logger.debug(f"Input tokens updated. New value: {counter.input.current}")
        logger.debug(f"Output tokens updated. New value: {counter.output.current}")

    def load_usage(self):
        """Load persisted usage data from file"""
        try:
            if self.data_file.exists():
                data = json.loads(self.data_file.read_text())
                for model, usage_data in data.items():
                    usage = Usage()
                    # Assuming usage_data has the structure to populate Usage object
                    # You'll need to adjust this based on actual saved data structure
                    usage.requests_per_minute.current = usage_data.get('requests_per_minute', {}).get('current', 0)
                    usage.requests_per_minute.timestamp = usage_data.get('requests_per_minute', {}).get('timestamp', time.time())
                    
                    usage.tokens_per_minute.input.current = usage_data.get('tokens_per_minute', {}).get('input', {}).get('current', 0)
                    usage.tokens_per_minute.input.timestamp = usage_data.get('tokens_per_minute', {}).get('input', {}).get('timestamp', time.time())
                    usage.tokens_per_minute.output.current = usage_data.get('tokens_per_minute', {}).get('output', {}).get('current', 0)
                    usage.tokens_per_minute.output.timestamp = usage_data.get('tokens_per_minute', {}).get('output', {}).get('timestamp', time.time())
                    
                    usage.tokens_per_day.input.current = usage_data.get('tokens_per_day', {}).get('input', {}).get('current', 0)
                    usage.tokens_per_day.input.timestamp = usage_data.get('tokens_per_day', {}).get('input', {}).get('timestamp', time.time())
                    usage.tokens_per_day.output.current = usage_data.get('tokens_per_day', {}).get('output', {}).get('current', 0)
                    usage.tokens_per_day.output.timestamp = usage_data.get('tokens_per_day', {}).get('output', {}).get('timestamp', time.time())
                    
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
                    'requests_per_minute': {
                        'current': usage.requests_per_minute.current,
                        'timestamp': usage.requests_per_minute.timestamp
                    },
                    'tokens_per_minute': {
                        'input': {
                            'current': usage.tokens_per_minute.input.current,
                            'timestamp': usage.tokens_per_minute.input.timestamp
                        },
                        'output': {
                            'current': usage.tokens_per_minute.output.current,
                            'timestamp': usage.tokens_per_minute.output.timestamp
                        }
                    },
                    'tokens_per_day': {
                        'input': {
                            'current': usage.tokens_per_day.input.current,
                            'timestamp': usage.tokens_per_day.input.timestamp
                        },
                        'output': {
                            'current': usage.tokens_per_day.output.current,
                            'timestamp': usage.tokens_per_day.output.timestamp
                        }
                    }
                }
            self.data_file.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"Error saving usage data: {e}")

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
