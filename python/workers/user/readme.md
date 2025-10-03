# User Workers

Custom open-source indicator workers.

## Usage
1. Create your worker inheriting from `AbstractWorker`
2. Implement `compute()` and `get_contract()`
3. Reference in scenario config: `"worker_types": ["USER/my_custom_worker"]`

## Example
See framework core workers for reference implementations.