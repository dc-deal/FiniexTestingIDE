# Blackbox Workers

IP-protected proprietary indicator workers.

## Usage
1. Add your proprietary workers here
2. They are automatically excluded from Git
3. Reference in scenario config: `"worker_types": ["BLACKBOX/my_secret_strategy"]`

## Security
All `.py` files (except `__init__.py`) are ignored by Git.