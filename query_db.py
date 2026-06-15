import sqlite3
import traceback

conn = sqlite3.connect('runbookmind.db')
cur = conn.cursor()

try:
    cur.execute("SELECT * FROM action_executions WHERE execution_id='exec_320e415ac22e'")
    print('ACTION_EXECUTIONS:', cur.fetchall())
except Exception as e:
    print(e)

try:
    cur.execute("SELECT * FROM executions WHERE execution_id='exec_320e415ac22e'")
    print('EXECUTIONS:', cur.fetchall())
except Exception as e:
    print(e)

try:
    cur.execute("SELECT * FROM node_executions WHERE execution_id='exec_320e415ac22e'")
    print('NODE_EXECUTIONS:', cur.fetchall())
except Exception as e:
    print(e)

try:
    cur.execute("SELECT * FROM jobs")
    jobs = cur.fetchall()
    for job in jobs:
        if 'exec_320e415ac22e' in str(job):
            print('JOB:', job)
except Exception as e:
    print(e)

try:
    # also check audit logs or standard application logs if we store them in the db
    pass
except Exception:
    pass

conn.close()
