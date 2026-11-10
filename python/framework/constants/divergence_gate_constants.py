"""
The divergence gate's code path, and the changes to it each release permits (#512).

A backtest is compared with a live session only where it computes what the session would have
computed. The gate therefore holds the import closure of the backtest's code path — at the
backtest's commit and at the session's — file by file: unchanged, changed in docstrings only, or
DECLARED below. A declaration names why the file may differ and pins the syntax tree it may have,
so a later change to a declared file is refused until its line here moves with it. A release with
no entry permits no change at all.

The categories:
- recording_only: the backtest records what it did, and computes nothing differently for it
- inputs_of_record: the backtest reads its inputs from the session's own records instead of the
  archive's
- algo_view: what a decision logic reads of its positions (#653); that it changes nothing for the
  run is shown by comparing the release with the merge, event for event
"""

# Where the backtest's code path starts: the scenario process, its executor, and every module of
# the data preparation the batch runs before it
GATE_ENTRY_POINTS = (
    'python/framework/process/process_main.py',
    'python/framework/process/process_executor.py',
)
GATE_ENTRY_DIRECTORIES = ('python/framework/data_preparation',)

# The tree the code path lies in, and the files of it the closure follows
GATE_SOURCE_DIRECTORY = 'python'
GATE_SOURCE_SUFFIX = '.py'

# Left out of the closure: the models the API serves. The backtest fills them for its report and
# never reads one back to compute.
GATE_EXCLUDED_PREFIXES = ('python/framework/types/api/',)

# The bound above which a pause counts as a gap — between two bars of the archive, inside a run of
# missing trade ids, or between two state frames of a session
GAP_BOUND_S = 120

# How far a backtest scenario's first or last tick may fall inside the compared window's edges —
# a pause shorter than the gap bound is no gap
WINDOW_EDGE_TOLERANCE_S = GAP_BOUND_S

# Release of the session → file → why it may differ, and the syntax tree it may have
ALLOWED_CHANGES_BY_RELEASE = {
    '1.5.0': {
        'python/framework/bars/warmup_history.py': {
            'categories': ('inputs_of_record',),
            'syntax_digest': '562f1cf062877b88a8d99f343d834faea94a623f923a420569f999e7141737d5'},
        'python/framework/constants/run_layout_constants.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'f672cc94ed5a2317dbc38f47c689db65d8afd6400ce07f9c149aca08ca5c645f'},
        'python/framework/data_preparation/broker_data_preparator.py': {
            'categories': ('inputs_of_record',),
            'syntax_digest': '603c8e78e9170dda0450644056a724f4e919791ef6dce3860429256120595c05'},
        'python/framework/decision_logic/core/test_probes/observer_probe.py': {
            'categories': ('algo_view',),
            'syntax_digest': '7c7116347abefdb487c18ad340ce9c719e603bd66c0a21c6461bfa30744ed539'},
        'python/framework/decision_logic/own_exit_levels.py': {
            'categories': ('algo_view',),
            'syntax_digest': '1ab06d5bd40f97b00dd688b1f57b63eaec25c611ce2b6e1f89b33b24e8a28025'},
        'python/framework/factory/decision_logic_factory.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'acbeb5096b1554c9c5df81c9cf0473e9533362f5e889e8e3503f849000f3bdad'},
        'python/framework/factory/trade_simulator_factory.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'cf80a92505cca3fb015de1846a926cfa8744e9dd636164da677c76b6b2c022af'},
        'python/framework/process/process_main.py': {
            'categories': ('recording_only',),
            'syntax_digest': '0900f14c6b9d4c4381164fe548774b1ccb7a22f4a562ca726945a3c2b78b3d43'},
        'python/framework/process/process_startup_preparation.py': {
            'categories': ('inputs_of_record',),
            'syntax_digest': '4aa19e71f2425d68ad0a29803163e690f681a6b9652771f6eabb86c70fd061be'},
        'python/framework/process/process_tick_loop.py': {
            'categories': ('recording_only',),
            'syntax_digest': '43c8139e0c4d531ded98913a9eff8ae047ab939f73502903a8fafdc2d822cc62'},
        'python/framework/process/state_frame_recorder.py': {
            'categories': ('recording_only',),
            'syntax_digest': '07ee851650069f93913a1b16693b8c4b9f205be9765997997fd9852396ec0d98'},
        'python/framework/reporting/builders/pending_orders_report_builder.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'a7ac9c7dd87a2915bb22c9d6572a4e37eb4e0d35f50a63374174f74abe910da9'},
        'python/framework/reporting/builders/portfolio_report_builder.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'dcf5e093f55d153b866504cff3beafa1f51fe1e784c7633bdb56e35b4180a908'},
        'python/framework/store/entry_members/run_entry_members.py': {
            'categories': ('recording_only',),
            'syntax_digest': '742d46f94df5353fa30518162e8fb1d495d8b490d21f05eaa66bb0c2a76a91a8'},
        'python/framework/trading_env/abstract_trade_executor.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'cf5c382f62426a20a59ba18459d6d33e82051c02afaa8961f7b909dbb5fa38fb'},
        'python/framework/trading_env/algo_masks/abstract_algo_mask.py': {
            'categories': ('algo_view',),
            'syntax_digest': '659632398a044d1574eb503ba5b476540a89267619227c31361066c664ee4f65'},
        'python/framework/trading_env/algo_masks/algo_position.py': {
            'categories': ('algo_view',),
            'syntax_digest': '0989e97ea653865239efe97de92703c283ef117aefcf6919e7998768f1a65493'},
        'python/framework/trading_env/decision_trading_api.py': {
            'categories': ('algo_view',),
            'syntax_digest': '35e6e782bb7297382d4ce984a759a2c6b973583267ac70e80b0d372e47e9cf3d'},
        'python/framework/trading_env/portfolio_manager.py': {
            'categories': ('algo_view',),
            'syntax_digest': '051f9e120a4b2a8b4b35de5c8fa96a2ae908dc1281f12af8bbee721495c632f1'},
        'python/framework/trading_env/simulation/trade_simulator.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'b8b0d3c5dd53bdc7192e28105f50b9ea7c87e20457107a105da1f8e5f8944f57'},
        'python/framework/types/config_types/autotrader_defaults_config_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': '14799e1e1f0aeef1772ed6303e670abd5a0ef3ce15b000c5c8794030536831ff'},
        'python/framework/types/config_types/backtesting_config_types.py': {
            'categories': ('recording_only', 'algo_view'),
            'syntax_digest': '0a9bf0a17b05f283737e9ef11fea4f96c8ba4e9a59ea038520420fc070c63b29'},
        'python/framework/types/config_types/verification_config_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'f2f44bec0bf82c54ce9c9329d883a059062b5ac3f5e21562e394fff9c7875086'},
        'python/framework/types/market_types/warmup_history_types.py': {
            'categories': ('inputs_of_record',),
            'syntax_digest': 'd92576a7621f6f9931f34bfde85d477b0340c0ae0bce389387377aef7de2e060'},
        'python/framework/types/portfolio_types/portfolio_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': '21615e812a50c31ea7640b5c766378b254661b48f131978dab8814e570d72afc'},
        'python/framework/types/probe_metadata_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': '66e28a09146f57f90031e452a1856e71cb232569ea76165d4cd6428ff7d1ea8a'},
        'python/framework/types/process_data_types.py': {
            'categories': ('recording_only', 'inputs_of_record', 'algo_view'),
            'syntax_digest': 'ad9f90b63235b0f680cbac555ea528cc7316ae8be95ddea8f5c06517ed883591'},
        'python/framework/types/process_table_types.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'c686714ca8e9ffbf28f0f27d79a5cbc28792e82a5b5ecd8f8bbb956d4a840ca1'},
        'python/framework/types/scenario_types/scenario_set_types.py': {
            'categories': ('inputs_of_record',),
            'syntax_digest': '057d0685e3160ab6867a1c65da3236d31c223bb60e4792040ab8be7ae9b80f63'},
        'python/framework/types/store_member_types.py': {
            'categories': ('recording_only',),
            'syntax_digest': '44c3f6c5f9ea358f836e51ea0bae2ff426051e3ad4aa395f5ed9ad11d40d70b0'},
        'python/framework/types/store_types.py': {
            'categories': ('recording_only',),
            'syntax_digest': '0395c1e2ade00aea7ce0f55b9b850098c3f79886118038062570b4d77ba479ce'},
        'python/framework/types/trading_env_types/decision_api_method_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': '443f483d7fd55c6f78c5d480a1de828982758e745c258d9c24c522a8b3e7c7cb'},
        'python/framework/utils/process_memory_reader.py': {
            'categories': ('recording_only',),
            'syntax_digest': '8dd074e6c4d28f3a956ca1e20d758e2a8e12b121ab8dff9d6c46a25c08171a32'},
    },
}
