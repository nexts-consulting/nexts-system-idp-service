from idp_extraction.main import app
import uvicorn
from idp_extraction.config import Settings

if __name__ == "__main__":
    s = Settings()
    uvicorn.run(app, host=s.host, port=s.port)
