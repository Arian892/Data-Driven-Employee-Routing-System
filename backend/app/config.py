from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]

class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    jwt_secret: str = Field(validation_alias=AliasChoices("jwt_secret", "jwt_secret_key"))
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(
        default=480,
        validation_alias=AliasChoices("jwt_expire_minutes", "access_token_expire_minutes"),
    )

    # Auto-routing scheduler: how often the background loop checks for a
    # completed request week that still needs routing.
    routing_check_interval_seconds: int = 60

    # Dev/test flag: bypass the Friday/Saturday request-window check so the
    # weekly flow can be exercised on any day of the week.
    request_window_override: bool = False

    # --- Routing engine -----------------------------------------------------
    # Which distance/duration provider the solver uses. "osrm" needs a local
    # OSRM server (car profile); "haversine"/"lightweight" is the dependency-free
    # straight-line fallback used where no OSRM server exists (e.g. Render).
    # Either way the solver falls back to haversine if OSRM is unreachable, so a
    # solve always completes.
    routing_engine: str = "osrm"
    osrm_base_url: str = "http://localhost:5000"
    # Separate engine for the pedestrian network (Case A walk times). The
    # routing_night algorithm needs real foot-network durations, not a
    # straight-line estimate, to decide whether a rider walks to a fixed stop.
    osrm_foot_base_url: str = "http://localhost:5001"
    osrm_timeout_seconds: float = 30.0
    # Health probe before the first real call; short, so a dead OSRM costs little.
    osrm_probe_timeout_seconds: float = 2.0
    # Haversine fallback only: straight-line km -> minutes.
    routing_average_speed_kmph: float = 40.0
    # Haversine fallback for the foot engine only (when OSRM foot is down).
    routing_walk_speed_kmph: float = 4.5

    @property
    def prefers_osrm(self) -> bool:
        """True unless the deploy explicitly asked for the lightweight engine.

        render.yaml ships ROUTING_ENGINE=lightweight; treat that (and the
        explicit "haversine") as "don't even probe OSRM".
        """
        return self.routing_engine.strip().lower() not in {"haversine", "lightweight"}

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

settings = Settings()
