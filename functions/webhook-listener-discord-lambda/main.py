from utils import get_safe_env

DISCORD_API_KEY = get_safe_env("DISCORD_API_KEY")


def handler(event, ctx):
    return {
        "statusCode": 200,
    }
