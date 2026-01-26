import os


def get_safe_env(var_name: str) -> str:
    """Retrieve an environment variable and ensure it is set.

    Args:
        var_name (str): The name of the environment variable to retrieve.
    Returns:
        str: The value of the environment variable.
    Raises:
        EnvironmentError: If the environment variable is not set.
    """
    value = os.getenv(var_name)
    if value is None:
        raise EnvironmentError(f"Environment variable '{var_name}' is not set.")
    return value
