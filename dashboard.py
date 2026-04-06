import eventlet
eventlet.monkey_patch()

from src.core.dashboard_live import run_dashboard

if __name__ == "__main__":
    run_dashboard(port=5000)