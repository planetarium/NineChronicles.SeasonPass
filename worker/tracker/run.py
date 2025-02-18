import subprocess

# Define the paths to the scripts
script1 = "worker.tracker.adv_boss_tracker"
script2 = "worker.tracker.courage_tracker"
script3 = "worker.tracker.world_clear_tracker"

# Start both scripts using subprocess.Popen
process1 = subprocess.Popen(["python", "-m", script1])
process2 = subprocess.Popen(["python", "-m", script2])
process3 = subprocess.Popen(["python", "-m", script3])

# Optionally wait for both processes to complete
process1.wait()
process2.wait()
process3.wait()
