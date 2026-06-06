from idp_preprocess.main import app
import uvicorn
from idp_preprocess.config import Settings

if __name__ == "__main__":
    s = Settings()
    uvicorn.run(app, host=s.host, port=s.port)
