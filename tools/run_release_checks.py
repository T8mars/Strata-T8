"""Run all CPU regression gates, including newly added upstream test modules.

Application checks need the pinned gguf-py source (bootstrap_portable.py --gguf-only).
Tokenizer checks additionally need an existing pack; they never fetch a model.
Formal releases use --strict to reject skipped tests or an empty suite.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tools')]

POSIX_CASES = (
    'tools.test_check_cuda_spills.Demangling.test_a_demangler_that_exits_nonzero_is_not_a_pass',
    'tools.test_check_cuda_spills.Demangling.test_a_demangler_that_hands_a_mangled_name_back_is_not_a_pass',
    'tools.test_check_cuda_spills.Demangling.test_a_demangler_that_prints_a_blank_name_is_not_a_pass',
    'tools.test_check_cuda_spills.Demangling.test_a_demangler_that_prints_the_wrong_number_of_lines_is_not_a_pass',
    'tools.test_check_cuda_spills.Demangling.test_a_working_demangler_reaches_the_verdict',
    'tools.test_check_cuda_spills.Demangling.test_an_unmangled_name_may_come_back_unchanged',
    'serve.test_lifeline.Lifeline.test_the_watcher_token_does_not_change_with_the_child_state',
    'serve.test_replicas.SharedArena.test_a_memfd_arena_warns_when_the_engines_will_not_all_fit',
    'serve.test_replicas.SharedArena.test_a_memfd_is_not_limited_by_dev_shm_and_is_closed_at_the_end',
    'serve.test_resource_warnings.Closed.test_the_cpu_and_ram_sampler_closes_its_files',
    'serve.test_resource_warnings.Closed.test_the_cpu_topology_closes_its_files',
    'serve.test_resource_warnings.Closed.test_the_hip_probes_close_their_files',
    'serve.test_vision_elastic_http.EncoderFailureHTTP.test_real_encoder_failure_retries_and_reaps_each_child',
    'serve.test_stream_ownership.ReplicaOwnership.test_an_undrainable_replica_is_not_handed_to_the_next_request',
    'tools.test_appimage_elf.RealStaticPie.test_a_filled_dynamic_section_does_not_start_and_is_found',
    'tools.test_appimage_elf.RealStaticPie.test_a_real_shared_library_with_a_runpath_passes',
    'tools.test_appimage_elf.RealStaticPie.test_the_real_program_runs_and_passes',
    'tools.test_appimage_elf.RealStaticPie.test_the_repack_script_check_passes_a_sound_bundled_rg_and_stops_a_damaged_one',

    'tools.test_setup_dsv41.CardsAndLayout.test_the_hardware_probe_falls_back_to_the_pci_ids',
    'tools.test_setup_dsv41.CardsAndLayout.test_the_pci_ids_name_the_card_when_kfd_and_rocminfo_say_nothing',
    'serve.test_cache_drop.CacheDrop.test_on',
    'serve.test_server.StartCrashReported.test_sigill_is_reported_not_retried',
    'serve.test_server.StartCrashReported.test_a_silent_exit_is_retried_without_claiming_the_gpu_runtime',
    'serve.test_server.StartCrashReported.test_the_runtimes_own_failure_is_named',
    'serve.test_lifeline.Lifeline.test_a_child_started_on_a_request_thread_outlives_that_thread_but_not_the_server',
    'serve.test_lifeline.Lifeline.test_the_portable_watcher_ends_the_child_when_the_server_is_killed',
    'tools.test_setup_mac.RepackGuard.test_a_live_lock_is_seen_a_stale_one_is_dropped',
    'tools.test_setup_mac.RepackGuard.test_start_refuses_while_a_repack_runs',
)

MODEL_PACK_CASES = (
    'tools.test_dflash2_pack.PackWithoutTorch.test_it_matches_the_torch_version_when_torch_is_here',
)
DEEPSEEK_TOKENIZER_CASE = 'serve.test_dsv41.Tokenizer.test_the_real_tokenizer_makes_the_ids_llama_cpp_made'


def applicable_suite(suite, *, windows, model_packs=False, deepseek_tokenizer=False):
    """Route the named POSIX cases to their actual host; do not mask any other skip."""
    selected, routed = unittest.TestSuite(), []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            children, omitted = applicable_suite(item, windows=windows, model_packs=model_packs,
                                                deepseek_tokenizer=deepseek_tokenizer)
            selected.addTests(children)
            routed.extend(omitted)
        elif windows and item.id() in POSIX_CASES:
            routed.append(item.id())
        elif not model_packs and item.id() in MODEL_PACK_CASES:
            routed.append(item.id())
        elif not deepseek_tokenizer and item.id() == DEEPSEEK_TOKENIZER_CASE:
            routed.append(item.id())
        else:
            selected.addTest(item)
    return selected, routed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['application', 'setup', 'tokenizer', 'deepseek-tokenizer', 'nodes', 'posix', 'model-packs'], default='application')
    parser.add_argument('--tokenizer', type=Path, help='Existing pack tokenizer for the tokenizer group')
    parser.add_argument('--node-tests', type=Path, help='Independent node test directory for the nodes group')
    parser.add_argument('--strict', action='store_true', help='Require executed tests with no failures, errors or skips')
    parser.add_argument('--test-deps', type=Path, help='Additional test-only dependencies, kept outside the shipped runtime')
    args = parser.parse_args()
    if args.test_deps is not None:
        if not args.test_deps.is_dir(): parser.error('--test-deps must be an existing directory')
        sys.path.append(str(args.test_deps.resolve()))
    if args.node_tests is not None and args.group != 'nodes':
        parser.error('--node-tests requires --group nodes')
    if args.group == 'nodes' and (args.node_tests is None or not args.node_tests.is_dir()):
        parser.error('nodes checks require --node-tests pointing to an existing test directory')
    from check_private_paths import private_paths
    forbidden = private_paths(ROOT)
    if forbidden:
        parser.error('Private planning files in Git index: '+', '.join(forbidden))
    if args.group == 'nodes':
        directory = args.node_tests.resolve()
        print(json.dumps({'group': args.group, 'node_tests': str(directory), 'strict': args.strict}), flush=True)
        suite = unittest.defaultTestLoader.discover(str(directory))
    elif args.group in ('tokenizer', 'deepseek-tokenizer'):
        if args.tokenizer is None or not (args.tokenizer/'vocab.json').is_file():
            parser.error('tokenizer checks require --tokenizer pointing to an existing pack tokenizer')
        if args.group == 'tokenizer':
            os.environ['STRATA_TOKENIZER'] = str(args.tokenizer.resolve())
            modules = ['serve.test_detok']
        else:
            os.environ['STRATA_DSV41_PACK'] = str(args.tokenizer.resolve())
            modules = [DEEPSEEK_TOKENIZER_CASE]
    elif args.group == 'setup':
        modules = ['tools.'+path.stem for path in sorted((ROOT/'tools').glob('test_setup_*.py'))]
    elif args.group == 'posix':
        if os.name == 'nt':
            parser.error('posix checks require a POSIX host')
        modules = ['tools.test_docker_entrypoint', *POSIX_CASES]
    elif args.group == 'model-packs':
        modules = list(MODEL_PACK_CASES)
    else:
        modules = ['serve.'+path.stem for path in sorted((ROOT/'serve').glob('test_*.py'))
                   if path.stem != 'test_detok']
        modules += ['tools.'+path.stem for path in sorted((ROOT/'tools').glob('test_*.py'))
                    if not path.stem.startswith('test_setup_')
                    and path.stem != 'test_nvfp4_local_pack'  # pytest + torch, run by the separate model-pack job
                    and not (os.name == 'nt' and path.stem == 'test_docker_entrypoint')]
    if args.group != 'nodes':
        print(json.dumps({'group': args.group, 'modules': modules, 'strict': args.strict}), flush=True)
        suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    suite, routed = applicable_suite(suite, windows=os.name == 'nt', model_packs=args.group == 'model-packs',
                                    deepseek_tokenizer=args.group == 'deepseek-tokenizer')
    if routed:
        print(json.dumps({'routed_to_posix_gate': [case for case in routed if case in POSIX_CASES],
                          'routed_to_model_pack_gate': [case for case in routed if case in MODEL_PACK_CASES],
                          'routed_to_local_deepseek_tokenizer_gate': [case for case in routed if case == DEEPSEEK_TOKENIZER_CASE]}), flush=True)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    passed = result.wasSuccessful() and (not args.strict or (result.testsRun > 0 and not result.skipped))
    print(json.dumps({'group': args.group, 'tests': result.testsRun, 'failures': len(result.failures),
                      'errors': len(result.errors), 'skipped': len(result.skipped),
                      'skipped_cases': [{'id': case.id(), 'reason': reason} for case, reason in result.skipped],
                      'seconds': round(time.monotonic()-started, 3),
                      'strict': args.strict, 'passed': passed}), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
