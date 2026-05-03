from typing import List

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from src.core.paths import PROMPTS_DIR
from src.interfaces.schemas.clarify import ClarificationQuestions, ProcessedInput

__all__ = ["AIGatekeeper", "ClarificationQuestions", "ProcessedInput", "prepare_clarification_questions"]



class AIGatekeeper:
    def __init__(self, api_key: str = None):
        self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)

        prompt_path = PROMPTS_DIR / "clarify_agent" / "clarify_agent.txt"
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_instructions = f.read()

        system_instructions = system_instructions.replace("{", "{{").replace("}", "}}")

        if "# TERMINATION CONDITION" in system_instructions:
            system_instructions = system_instructions.split("# TERMINATION CONDITION")[0]

        mapping_instructions = """
        
        ### ANTI-PERFECTIONIST RULE:
        - If the user provides most critical information, DO NOT ask for more.
        - Make logical assumptions for minor details (like resistor values, bypass caps) and state them in the 'refined_intent' later.
        - Set 'is_clear' to True as soon as you have enough info to reasonably design the circuit.
        
        ### MEMORY & EXTRACTION:
        - 'extracted_parameters': CUMULATIVE dictionary of ALL specs found in history.
        - NEVER re-ask a question that has already been answered.
        
        ### MAPPING:
        1. 'extracted_parameters': Dictionary of extracted details. YOU MUST POPULATE THIS.
           Example: {{"input_voltage": "12V", "output_voltage": "5V"}}
        2. 'clarification_question': The next focused question (if not clear).
        3. 'is_clear': True if ready for design.
        4. 'refined_intent': Detailed # OUTPUT TEMPLATE (only if is_clear is True).
        """

        self.prompt = ChatPromptTemplate.from_messages([
            ("system", system_instructions),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{user_text}"),
            ("system", "IMPORTANT: You MUST return your response as a JSON object matching this structure:\n" + 
             '{{"is_clear": bool, "refined_intent": "string", "extracted_parameters": {{"key": "val"}}, "clarification_question": "string"}}\n' +
             mapping_instructions)
        ])

    def process(self, text: str, history: List[BaseMessage] = None) -> ProcessedInput:
        """Parse raw user text into a structured ProcessedInput."""
        json_llm = self.llm.bind(response_format={"type": "json_object"})
        chain = self.prompt | json_llm
        
        response = chain.invoke({
            "user_text": text,
            "history": history or []
        })
        
        import json
        data = json.loads(response.content)
        return ProcessedInput(**data)


def prepare_clarification_questions(
    processed: ProcessedInput,
    max_questions: int = 3
) -> List[str]:
    """Generate clarification questions when the prompt lacks enough detail to proceed."""
    if processed.is_clear:
        return []

    if processed.clarification_question:
        seed_questions = [processed.clarification_question]
    else:
        seed_questions = []

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
    structured_llm = llm.with_structured_output(ClarificationQuestions, method="function_calling")
    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are an expert clarification agent for circuit blueprint. Generate concise, targeted questions to resolve missing
            information about circuit definition in the user's request. Keep each question short and actionable. Return only questions."""
        ),
        (
            "human",
            """User intent summary: {refined_intent}
             Existing clarification question: {clarification_question}
             Extracted parameters: {extracted_parameters}
             Max questions: {max_questions}

              Generate {max_questions} or fewer questions if enough information is already present."""
        )
    ])

    chain = prompt | structured_llm
    result = chain.invoke({
        "refined_intent": processed.refined_intent,
        "clarification_question": processed.clarification_question or "",
        "extracted_parameters": processed.extracted_parameters,
        "max_questions": max_questions
    })

    questions = [q.strip() for q in result.questions if q.strip()]
    if seed_questions:
        for question in reversed(seed_questions):
            if question not in questions:
                questions.insert(0, question)

    return questions[:max_questions]