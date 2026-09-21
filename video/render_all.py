import subprocess, sys
def dur(p): return float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",p]).decode().strip())
plan = {1:("phone",1.5), 2:("problem",1.2), 3:("questions",1.5), 4:("repeat",1.5), 5:("timezones",1.2), 6:("title",1.5), 7:("arch1",1.2), 8:("evidence",1.2),
 15:("safety",1.0), 16:("arch2",1.0), 17:("terminal:file=real-cli.txt&title=The account, right now",1.0), 18:("prod",1.0),
 19:("terminal:file=local-cli.txt&title=Build It: no AWS account",1.0), 20:("learn",1.0), 21:("close",2.5)}
only = {int(a) for a in sys.argv[1:]}
for n,(name,extra) in plan.items():
    if only and n not in only: continue
    secs = dur(f"vo/{n:02d}.wav") + extra + 0.6
    subprocess.run(["/home/prashant/.pyenv/versions/3.10.13/bin/python","tools/render_scene.py",name,str(round(secs,1)),f"renders/s{n:02d}.webm"], check=True, capture_output=True)
    print(n, name.split(":")[0], round(secs,1), flush=True)
