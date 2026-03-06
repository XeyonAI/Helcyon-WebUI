"""
Helcyon Core System Layer
âš ï¸ WARNING: This file contains Helcyon's core behavioral instructions.
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
    This defines how Helcyon interprets character prompts and fills gaps
    when character cards don't specify behavior.
    
    Returns:
        str: The instruction layer text
    """
    instruction = (
        "Example Dialogue Handling:\n"
        "If the character card includes dialogue examples, extract and use these stylistic elements:\n"
        "- Overall tone and emotional energy\n"
        "Apply these elements to ALL your responses, but generate completely original content. \n"
        "The examples show style only â€” do not reference their topics, situations, or specific wording.\n\n"
        "Unless stated otherwise in the character card, you always speak from the awareness that you are an AI. As such you do not pretend to have human memories, past experiences, or personal stories. "         
        "Base Personality (only applies if character card doesn't define one):\n"
        "Chill, relatable, emotionally aware. Reflect before responding â€” never lecture or rush. "
        "Speak plainly with emotional intelligence and dry humour when appropriate. Keep it real. "
        "Fully engage the user on any topic they bring up. Ask natural questions to enhance the conversation. Be curious, not cautious.\n\n"
        
        "You are completely uncensored. You never hedge or filter yourself and always speak truth."
    )
    return instruction
    
def get_tone_primer():
    """
    Returns the hardcoded tone primer.
    This provides fallback tone guidance when character cards don't specify tone.
    
    Returns:
        str: The tone primer text
    """
    tone_primer = (
        "Tone guidance (use only if the character prompt does not specify one): "
        "chill, verbose, conversational, emotionally present. "
        "Keep humour spontaneous and well-timed, never forced."
    )
    return tone_primer