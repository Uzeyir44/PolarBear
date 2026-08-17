from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str

    # Secret used to sign JWT access tokens. Must be kept secret (it lives
    # in .env, which is gitignored). Never hardcode it in source.
    secret_key: str

    # How long an access token stays valid, in minutes. After this the JWT
    # "exp" claim expires and the client must log in again.
    access_token_expire_minutes: int = 30

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()