"""Start the website: python run.py

It listens on 127.0.0.1:8000, which only this laptop can open.
"""

from waitress import serve

from jobhunter import config, create_app

if __name__ == "__main__":
    app = create_app()
    print(f"job-hunter is running at http://{config.HOST}:{config.PORT}  (Ctrl+C to stop)")
    serve(app, host=config.HOST, port=config.PORT, threads=4)
