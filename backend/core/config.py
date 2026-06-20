from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"
    kubeconfig_path: str = ""
    insforge_url: str = "https://pus66i6p.eu-central.insforge.app"
    insforge_anon_key: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
