import os
import traceback
import launcher
import util.resource

if 'WINEDEBUG' not in os.environ:
    os.environ['WINEDEBUG'] = '-all'

# data/env.env подхватывается до всего остального (ROBLOSECURITY и пр.)
util.resource.load_env_file()

INTERRUPT_MESSAGE = '** RECEIVED Ctrl+C **'

if __name__ == '__main__':
    try:
        launcher.read_eval_loop()
    except KeyboardInterrupt:
        print(INTERRUPT_MESSAGE)
    except Exception as e:
        traceback.print_exc()
        print(str(e))
