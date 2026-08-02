"""LLM factory for OpenAI-compatible chat models."""

from langchain_openai import ChatOpenAI
from loguru import logger

from app.config import config


class LLMFactory:
    """Create chat models through an OpenAI-compatible endpoint."""

    AUTODL_BASE_URL = "https://www.autodl.art/api/v1"
    DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    @staticmethod
    def create_chat_model(
        model: str | None = None,
        temperature: float = 0.7,
        streaming: bool = True,
        base_url: str | None = None,
        api_key: str | None = None,
        max_tokens: int | None = None,
    ) -> ChatOpenAI:
        """Create a LangChain ChatOpenAI model using project configuration."""
        selected_model = model or config.autodl_model
        selected_base_url = base_url or config.autodl_base_url or LLMFactory.AUTODL_BASE_URL
        selected_api_key = api_key or config.autodl_api_key

        if not selected_api_key:
            raise ValueError("请配置 AUTODL_API_KEY")

        llm = ChatOpenAI(
            model=selected_model,
            temperature=temperature,
            streaming=streaming,
            base_url=selected_base_url,
            api_key=selected_api_key,
            max_tokens=max_tokens,
        )

        logger.info("LLM 初始化完成: model={}, base_url={}", selected_model, selected_base_url)
        return llm


llm_factory = LLMFactory()
