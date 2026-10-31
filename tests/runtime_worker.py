"""Subprocess-only interruption fixture; not a production CLI option."""
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from content_planner.runtime import Runtime

if __name__ == '__main__':
    runtime = Runtime(sys.argv[1], sys.argv[2])
    if sys.argv[4] == 'crash':
        runtime.drive(sys.argv[3], after_action=lambda event: os._exit(23))
    elif sys.argv[4] == 'crash_save':
        import content_planner.runtime as module
        original = module.persist_state
        def crash_save(conn, task_id, state):
            if state['_saved']:
                os._exit(24)
            return original(conn, task_id, state)
        module.persist_state = crash_save
        runtime.drive(sys.argv[3])
    elif sys.argv[4] == 'hold':
        from content_planner.offline import OfflinePolicy
        class Hold(OfflinePolicy):
            def next_action(self, context):
                Path(sys.argv[5]).write_text('ready', encoding='utf-8')
                deadline = time.monotonic() + 15
                while not Path(sys.argv[6]).exists():
                    if time.monotonic() > deadline:
                        raise TimeoutError('Test release signal missing')
                    time.sleep(0.05)
                return super().next_action(context)
        runtime.drive(sys.argv[3], policy=Hold())
