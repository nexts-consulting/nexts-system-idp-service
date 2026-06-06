from idp_orchestrator.main import app
import uvicorn
from idp_orchestrator.config import Settings

if __name__ == "__main__":
    s = Settings()
    uvicorn.run(app, host=s.host, port=s.port)
