import hmac
import logging
from json import dumps
from os import X_OK, access, getenv, listdir
from os.path import join
from pathlib import Path
from subprocess import PIPE, Popen
from sys import stderr, exit
from traceback import print_exc
import yaml

from flask import Flask, abort, request


def get_secret(name):
    """Tries to read Docker secret or corresponding environment variable.

    Returns:
        secret (str): Secret value.

    """
    secret_path = Path('/run/secrets/') / name

    try:
        with open(secret_path, 'r') as file_descriptor:
            # Several text editors add trailing newline which may cause troubles.
            # That's why we're trimming secrets' spaces here.
            return file_descriptor.read() \
                .strip()
    except OSError as err:
        variable_name = name.upper()
        logging.debug(
            'Can\'t obtain secret %s via %s path. Will use %s environment variable.',
            name,
            secret_path,
            variable_name
        )
        return getenv(variable_name)


logging.basicConfig(stream=stderr, level=logging.INFO)

# Collect all scripts now; we don't need to search every time
# Allow the user to override where the hooks are stored
HOOKS_DIR = getenv("WEBHOOK_HOOKS_DIR", "/app/hooks")
scripts = [join(HOOKS_DIR, f) for f in sorted(listdir(HOOKS_DIR))]
scripts = [f for f in scripts if access(f, X_OK)]
if not scripts:
    logging.error("No executable hook scripts found; did you forget to"
                  " mount something into %s or chmod +x them?", HOOKS_DIR)
    exit(1)

# Get application secret
webhook_secret = get_secret('webhook_secret')
if webhook_secret is None:
    logging.error("Must define WEBHOOK_SECRET")
    exit(1)

# Get branch list that we'll listen to, defaulting to just 'master'
branch_whitelist = getenv('WEBHOOK_BRANCH_LIST', 'master').split(',')

# Our Flask application
application = Flask(__name__)

# Keep the logs of the last execution around
responses = {}

with open("config.yml", "r") as stream:
    try:
        CONFIG = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        logging.error(exc)
        raise exc


@application.route('/', methods=['POST'])
def index():
    global webhook_secret, branch_whitelist, scripts, responses

    repository = request.get("repository", {"repo_name": None}).get("repo_name")
    tag = request.get("push_data", {"tag": None}).get("tag")
    pusher = request.get("push_data", {"pusher": None}).get("pusher")
    # Respond to ping properly

    if repository not in CONFIG.get("repositories", {}).keys():
        logging.info("Not a push event, aborting")
        abort(403)
    if pusher not in CONFIG[repository].get("pushers", [pusher]):
        logging.info("Pusher not configured")
        abort(403)
    if tag not in CONFIG[repository].get("tags", ["latest"]):
        logging.info("Pusher not configured")
        abort(403)
    # Run scripts, saving into responses (which we clear out)
    responses = {}
    for script in scripts:
        proc = Popen([script, tag], stdout=PIPE, stderr=PIPE)
        stdout, stderr = proc.communicate()
        stdout = stdout.decode('utf-8')
        stderr = stderr.decode('utf-8')

        # Log errors if a hook failed
        if proc.returncode != 0:
            logging.error('[%s]: %d\n%s', script, proc.returncode, stderr)

        responses[script] = {
            'stdout': stdout,
            'stderr': stderr
        }

    return dumps(responses)


@application.route('/logs', methods=['GET'])
def logs():
    return dumps(responses)


# Run the application if we're run as a script
if __name__ == '__main__':
    logging.info("All systems operational, beginning application loop")
    application.run(debug=False, host='0.0.0.0', port=8000)
