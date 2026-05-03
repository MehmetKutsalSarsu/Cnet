from src.agents.clarify.clarify import AIGatekeeper, ProcessedInput

def handle_user_input(text: str) -> ProcessedInput:

    gatekeeper = AIGatekeeper()
    return gatekeeper.process(text)
