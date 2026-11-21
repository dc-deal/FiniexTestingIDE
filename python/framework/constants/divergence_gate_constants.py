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

# Release of the session → the interpreter, major.minor, its declarations' syntax trees were pinned
# under; the report refuses to judge them under another rather than read every pin as changed
PINNED_INTERPRETER_BY_RELEASE = {'1.5.0': '3.12'}

# Release of the session → file → why it may differ, and the syntax tree it may have
ALLOWED_CHANGES_BY_RELEASE = {
    '1.5.0': {
        'python/framework/bars/warmup_history.py': {
            'categories': ('recording_only', 'inputs_of_record'),
            'syntax_digest': '9d261784d029d9974d7b52875fecce318c7dee7cf9e56e6c4cbded9145f8e1ce'},
        'python/framework/constants/run_layout_constants.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'ad1a97232778f02a14b68712727fd21f4550928a1136cf6738256e63e4144fb3'},
        'python/framework/data_preparation/broker_data_preparator.py': {
            'categories': ('inputs_of_record',),
            'syntax_digest': 'fbbb04f3aaf74f6a6976262c635e469811399827696ed80f92b72b2a0b7f8264'},
        'python/framework/decision_logic/core/test_probes/observer_probe.py': {
            'categories': ('algo_view',),
            'syntax_digest': '5931ebe13316f58b06f53440d41add644b31bbe9a03efe32884ab7bcd2018db6'},
        'python/framework/decision_logic/own_exit_levels.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'ae4551114217e0cb375a5bef192349f5d487da6d6b0783b101189fed8dd7ebae'},
        'python/framework/factory/decision_logic_factory.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'a4684ec96fdc0160654cedf181239edddf44ff7e23c6e28a864dcbb01f407aa2'},
        'python/framework/factory/trade_simulator_factory.py': {
            'categories': ('algo_view',),
            'syntax_digest': '4d560c1d6627ab388159dfd36463415f53984e299633c0b8cc47d35543ad429b'},
        'python/framework/process/process_main.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'bd0bc40a59adb98664e0b0ee3584f5de53e4a712cc7b2389aa9911ca07a51133'},
        'python/framework/process/process_startup_preparation.py': {
            'categories': ('recording_only', 'inputs_of_record'),
            'syntax_digest': '678b87a4486f349348c772d8cf03ec1dadcf248970f41fabbe5527aba3236794'},
        'python/framework/process/process_tick_loop.py': {
            'categories': ('recording_only',),
            'syntax_digest': '8c417ed000909abc92891f97d709bc3a8d93a8f2f158c56de66bf3eeac35d464'},
        'python/framework/process/state_frame_recorder.py': {
            'categories': ('recording_only',),
            'syntax_digest': '03d4c47ef36f9d8a74d2bec579adaf48ec11c50a17e141949388258a3090b683'},
        'python/framework/reporting/builders/pending_orders_report_builder.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'd141948a41075e3567deb9d0747daf9d1d3e9ed05046119c481e4b093198a201'},
        'python/framework/reporting/builders/portfolio_report_builder.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'ec2b7334c519e57121fdaed546ad7625dd6a03f30e5afa99296a14fc08220cd9'},
        'python/framework/store/entry_members/run_entry_members.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'f75b4e8a86144082835fc8991049547519207a43ae113df8a4e4cbe8e4204ec5'},
        'python/framework/trading_env/abstract_trade_executor.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'af72ae6e63f10d2dd7e1f6990f350b4f9c3536f59d15952ecf0df8e9218ee698'},
        'python/framework/trading_env/algo_masks/abstract_algo_mask.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'd81a3ec7e9a30caa91a412d127e031fcb9d2b87421f857870b751ffdd6589eec'},
        'python/framework/trading_env/algo_masks/algo_position.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'd3b80c7626b5f7f7719de855fa95f0758ff46c18af5d8b40a9dac80fc695072f'},
        'python/framework/trading_env/decision_trading_api.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'f7d7cd1df7836e34cd6d34ff5d800bbcb4dbbfd2cf5e31c28be19555abb4b387'},
        'python/framework/trading_env/portfolio_manager.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'cc2d1752202f5aabb50c20faacc6b1228c9cc974ed574cbc9f0e5647af2ddb75'},
        'python/framework/trading_env/simulation/trade_simulator.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'eae7130def3f8663a86ad1ebab2c691da4cb9d2af2d065cb5d2e7e2e97a7a023'},
        'python/framework/types/config_types/autotrader_defaults_config_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': '7770904c2f7f1e9606ad64ecf617084c73631cc3760b7b4f5fad1288eb89fcff'},
        'python/framework/types/config_types/backtesting_config_types.py': {
            'categories': ('recording_only', 'algo_view'),
            'syntax_digest': '58604a10ac29453f8ff1e08a8501e3973ebfb2c82d2b37a0745618e2d7d987fb'},
        'python/framework/types/config_types/verification_config_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'a9d92fe4415c7a44dd0dccf20e73994d51f26c8fbc94d33ab4c082bbe052f1ae'},
        'python/framework/types/market_types/warmup_history_types.py': {
            'categories': ('inputs_of_record',),
            'syntax_digest': '8ac700318ae4b35736ac52ef1adff195e626a135aa696446ce427791ff1feb5b'},
        'python/framework/types/portfolio_types/portfolio_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'ce9b6202201b893881d7879334e715d8fdd2d2c9d941a0a327d40f3cbd2400e4'},
        'python/framework/types/probe_metadata_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': '3255ff6769e2113e22b8523c2dcaac59c7f1eecfcd387e4a23b4237856f48225'},
        'python/framework/types/process_data_types.py': {
            'categories': ('recording_only', 'inputs_of_record', 'algo_view'),
            'syntax_digest': 'd2f33eb952beb1b27a2f9af7b2c666072f381413a954a18a1345f4d545e53484'},
        'python/framework/types/process_table_types.py': {
            'categories': ('recording_only',),
            'syntax_digest': '153a2a47bd862a3f3804837f9ca53961f544ba8ecaaafc7883bf3e572ea2a5e7'},
        'python/framework/types/scenario_types/scenario_set_types.py': {
            'categories': ('inputs_of_record',),
            'syntax_digest': '42aa1b5cc2d319b97c2147cf8d6cdeeabf064ffb4bfe5e14965337017aaf146e'},
        'python/framework/types/store_member_types.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'b143f6df37338a1aa1f19d44a94d9972c3a69f0c32a3ebc9ba86c13135eb04c0'},
        'python/framework/types/store_types.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'e0dbdcf48d1203536885443ced55a809bd00647f962b34bcea061ded5343873a'},
        'python/framework/types/trading_env_types/decision_api_method_types.py': {
            'categories': ('algo_view',),
            'syntax_digest': 'ffb10e70bd4223e232c8794cd842f7db7e579acab6f675fb473711c09b14ec0f'},
        'python/framework/utils/process_memory_reader.py': {
            'categories': ('recording_only',),
            'syntax_digest': 'f163215cc6c79b9f9d7ee65d2e8c7b39e8f1e1dad1fc0e58243f67f16fa13fbc'},
    },
}
