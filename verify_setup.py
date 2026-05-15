import sys
import os

# Add backend directory to sys.path to import modules
sys.path.append(os.path.join(os.getcwd(), 'backend'))

try:
    from backend.monitor import poll_repo
    from backend.database import Base, engine
    
    print("Initializing database...")
    Base.metadata.create_all(bind=engine)
    
    print("Testing GitHub connection and polling...")
    poll_repo()
    print("Polling completed successfully. Check the output above for any errors.")
except Exception as e:
    print(f"An error occurred during verification: {e}")
