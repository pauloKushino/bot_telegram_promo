from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração tipada via variáveis de ambiente (ver .env.example)."""

    # Telegram
    BOT_TOKEN: str
    CHANNEL_ID: int
    ADMIN_TELEGRAM_IDS: list[int]

    # Banco
    DATABASE_URL: str

    # IA — NVIDIA NIM (API compatível com OpenAI)
    NVIDIA_API_KEY: str
    AI_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    AI_MODEL: str = "meta/llama-3.3-70b-instruct"
    AI_TEMPERATURE: float = 0.2

    # Shopee Afiliados
    SHOPEE_APP_ID: str = ""
    SHOPEE_SECRET: str = ""
    SHOPEE_GRAPHQL_URL: str = "https://open-api.affiliate.shopee.com.br/graphql"

    # Userbot Telethon (SÓ testes E2E em scripts/; o app nunca usa)
    TG_API_ID: int = 0
    TG_API_HASH: str = ""
    TG_PHONE: str = ""
    TG_SESSION_FILE: str = "userbot.session"

    # Comportamento
    COLLECT_INTERVAL_MIN: int = 20
    MAX_POSTS_PER_DAY: int = 20
    MIN_POST_INTERVAL_MIN: int = 10
    QUIET_HOURS: str = "00:00-07:00"
    TIMEZONE: str = "America/Sao_Paulo"
    MIN_HISTORY_DAYS: int = 7
    MIN_DROP_PCT: float = 0.15
    MIN_SELLER_RATING: float = 4.5
    MIN_SALES_COUNT: int = 5
    MIN_OFFICIAL_CONFIDENCE: float = 0.7
    LOG_LEVEL: str = "INFO"

    @field_validator("ADMIN_TELEGRAM_IDS", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        """Aceita "1,2,3", inteiro único ou lista, para facilitar o .env."""
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(part.strip()) for part in value.split(",") if part.strip()]
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # o .env pode conter variáveis só do docker-compose (POSTGRES_*)
    )


settings = Settings()
