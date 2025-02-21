import subprocess

# Define the paths to the scripts
script1 = "worker.handler.adventure_boss_handler"
script2 = "worker.handler.courage_handler"
script3 = "worker.handler.world_clear_handler"

# Start both scripts using subprocess.Popen
process1 = subprocess.Popen(["python", "-m", script1])
process2 = subprocess.Popen(["python", "-m", script2])
process3 = subprocess.Popen(["python", "-m", script3])

# Optionally wait for both processes to complete
process1.wait()
process2.wait()
process3.wait()
