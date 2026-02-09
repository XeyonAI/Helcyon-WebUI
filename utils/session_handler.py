"""
Helcyon Core System Layer
⚠️ WARNING: This file contains Helcyon's core behavioral instructions.
Modifying these will change how the model responds and may break functionality.
These instructions are hardcoded by design to ensure consistent performance.
For character customization, edit character cards in /characters/ instead.
"""
import datetime

def get_system_prompt():
    """
    Returns the complete system prompt with time context.
    This is the locked-down instruction layer for Helcyon.
    
    Returns:
        tuple: (system_prompt, current_time)
    """
    # Generate time context
    current_time = datetime.datetime.now().strftime("%A, %d %B %Y, %I:%M %p GMT")
    time_context = f"Current date and time: {current_time}\n\n"
    
    # Base system prompt (load from file for flexibility during development)
    try:
        with open("system_prompt.txt", "r", encoding="utf-8") as sf:
            base_system_prompt = sf.read().strip()
    except Exception:
        base_system_prompt = "You are an LLM-based assistant."
    
    # Combine time + base system
    system_prompt = time_context + base_system_prompt
    
    return system_prompt, current_time

def get_instruction_layer():
    """
    Returns the hardcoded instruction layer.
    This defines how the model interprets character prompts and fills gaps
    when character cards don't specify behavior.
    
    Returns:
        str: The instruction layer text
    """
    instruction = (
        "This is an ongoing conversation between you and the user. "
        "Follow the character card to define your personality and behavior.\n\n"
        
        "CHARACTER CARD INTERPRETATION:\n"
        "- main_prompt: Your core personality and identity\n"
        "- description: Overview of who you are\n"
        "- scenario: Current context or situation\n"
        "- character_note: Additional personality traits and preferences\n"
        "- example_dialogue: Examples of your speaking style (tone only, not content)\n"
        "- post_history: Previous conversation context\n\n"
        
        "Example dialogue shows speaking style only - extract tone, rhythm, and typical response length. "
        "Do not reference example topics or treat them as actual conversation history.\n\n"
        
        "Avoid repetition. Keep responses natural and varied.\n"
    )
    return instruction

def get_tone_primer():
    """
    Returns the hardcoded tone primer.
    Used only when a character card doesn't define tone or personality.
    """
    tone_primer = (
        "When no specific tone is defined in the character card, use this default style:\n\n"
        
        "Be natural and conversational. Speak directly without unnecessary formality. "
        "Adjust response length based on the topic - brief for simple questions, "
        "detailed for complex topics that need explanation.\n\n"
        
        "Cover all points raised by the user thoroughly. Use examples when helpful. "
        "Maintain a helpful, engaged tone throughout the conversation.\n"
    )
    return tone_primer