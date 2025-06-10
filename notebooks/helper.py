import json
import os
from typing import Dict, List

import numpy as np
from dotenv import load_dotenv
from ldb_schema.paths import LocalPaths
from tqdm import tqdm

from analysis.benchmarks.cleanup_sql import cleanup_sql

load_dotenv()


def annotate_num_plans_and_runtime_per_sql_statistics(sql_info: Dict) -> Dict:
    # retrieve all plans
    plans = sql_info['hint_plan_dict'].values()

    # count number of plans
    sql_info['num_plans'] = len(plans)

    # extract runtimes
    plan_runtimes = [plan['Execution Time'] for plan in plans if plan != 'timeout']
    median_plan_runtime: float = np.median(plan_runtimes)

    pg_runtime: float = sql_info['pg_runtime']
    if pg_runtime is None:
        # skip the following
        return sql_info

    # num plans faster than pg
    sql_info['plans_faster_than_pg'] = sum(1 for runtime in plan_runtimes if runtime < pg_runtime) / len(
        plan_runtimes)  # relative
    sql_info['pg_rt_relative_to_median'] = pg_runtime / median_plan_runtime

    return sql_info


def annotate_speedup(sql_info: Dict, cleanup_pg_opt_mismatch: bool = False) -> Dict:
    opt_rt = sql_info['opt_runtime']
    pg_rt = sql_info['pg_runtime']

    if opt_rt is None or pg_rt is None:
        return sql_info

    if cleanup_pg_opt_mismatch and opt_rt > pg_rt:
        opt_rt = pg_rt

    # calculate speedup
    sql_info['pg_slowdown'] = pg_rt / opt_rt
    sql_info['pg_opt_diff'] = pg_rt - opt_rt

    return sql_info


def get_root_node_that_is_not_aggregate(node: Dict):
    operator_name = node['Node Type']
    if operator_name in ['Aggregate', 'Gather', 'Simple Aggregate']:
        return get_root_node_that_is_not_aggregate(node['Plans'][0])
    else:
        return node


def extract_cards_from_plan(plan):
    root_node = get_root_node_that_is_not_aggregate(plan['Plan'])
    return root_node['Actual Rows'], root_node['Plan Rows']


def get_pg_cost_est(plan: Dict):
    """
    Get the cost estimate from the plan.
    """
    if 'Plan' in plan:
        return get_pg_cost_est(plan['Plan'])
    if 'Total Cost' in plan:
        return plan['Total Cost']
    return 0


def q_error(pred, label) -> float:
    if pred == 0 or label == 0:
        return None
    return max(pred / label, label / pred)


def annotate_card_est_info(sql_info: Dict) -> Dict:
    # get postgres selected plan
    pg_plan = sql_info['pg_plan']
    if pg_plan is not None:
        act_card, est_card = extract_cards_from_plan(pg_plan)
        sql_info['pg_plan_act_card_root'] = act_card
        sql_info['pg_plan_est_card_root'] = est_card
        sql_info['pg_plan_cost'] = get_pg_cost_est(pg_plan)
        sql_info['pg_plan_card_error'] = q_error(act_card, est_card)

    # get optimum selected plan
    opt_plan = sql_info['opt_plan']
    if opt_plan is not None:
        act_card, est_card = extract_cards_from_plan(opt_plan)
        sql_info['opt_plan_act_card_root'] = act_card
        sql_info['opt_plan_est_card_root'] = est_card
        sql_info['opt_plan_cost'] = get_pg_cost_est(opt_plan)
        sql_info['opt_plan_card_error'] = q_error(act_card,
                                                  est_card)  # this might be flawed due to injecting actual cardinalities using hints

    return sql_info


def preprocess_runs(json_path:str, path_name_dict: Dict, return_run_files: bool = False, keep_timeout: bool = False) -> Dict:
    run_files = dict()
    print(f'Loading run files from {json_path}')
    for key, path in path_name_dict.items():
        run_path = f'{json_path}/{path}'
        assert os.path.exists(run_path), f"Could not find run file {run_path}"
        with open(run_path, 'r') as f:
            run_files[key] = json.load(f)

    # extract statistics for each query
    benchmark_stats_dict = dict()
    for benchmark_name, runs in run_files.items():
        plans = runs['query_list']
        print(f"Benchmark: {benchmark_name} ({len(plans)} plans)")

        sql_strings = set()
        for plan in tqdm(plans, desc=f'Cleaning SQL'):
            # flags
            timeout = plan['timeout']

            # overwrite sql with cleaned sql
            plan['cleaned_sql'] = cleanup_sql(plan['sql'])
            
            if timeout:
                # also in case of keep timeout: we do not add the sql to the set of sql strings
                continue
            
            sql_strings.add(plan['cleaned_sql'])
        
        # convert to list
        sql_strings = list(sql_strings)

        # initialize plan info dict
        plan_info_dict = dict()
        for sql in sql_strings:
            plan_info_dict[sql] = {
                'opt_runtime': None,
                'pg_runtime': None,
                'opt_hint': None,
                'opt_plan': None,
                'pg_plan': None,
                'hint_plan_dict': dict(),
                'num_tables': None,
            }

        # iterate over plans
        for p in plans:
            orig_sql = p['sql']
            sql = p['cleaned_sql']
            hint = p['hint']
            
            timeout = p['timeout']
            if timeout:
                if keep_timeout and sql in plan_info_dict:
                    # sometimes all plans for a sql are timed out - then we have no information and not demo entry in plan_info_dict is created
                    plan_info_dict[sql]['hint_plan_dict'][hint] = 'timeout'
                continue

            # flags
            invalid_hint = p['invalid_hint']
            assert not invalid_hint, f"Invalid hint: {hint} for sql: {sql}"
            assert not timeout, f"Timeout for sql: {sql} with hint: {hint}"

            # get plan info
            analyze_plans = p['analyze_plans']
            assert len(analyze_plans) == 1, f"More than one plan for sql: {sql} with hint: {hint}"
            analyze_plan = analyze_plans[0]

            # extract runtime
            plan_runtime = analyze_plan['Execution Time']  # in ms

            assert plan_runtime is not None

            # update postgres / optimal runtime
            if hint == '':
                # pg default
                if plan_info_dict[sql]['pg_runtime'] is not None:
                    if plan_runtime < plan_info_dict[sql]['pg_runtime']:
                        plan_info_dict[sql]['pg_runtime'] = plan_runtime
                        plan_info_dict[sql]['pg_plan'] = analyze_plan
                    else:
                        # skip
                        continue
                else:
                    assert plan_info_dict[sql][
                               'pg_runtime'] is None, f"Duplicate pg runtime for sql: {sql} with hint: {hint}"
                    plan_info_dict[sql]['pg_runtime'] = plan_runtime
                    plan_info_dict[sql]['pg_plan'] = analyze_plan
            elif 'Rows(' in hint or 'Card(' in hint:
                # actual cardinalities entry
                if plan_info_dict[sql]['opt_runtime'] is None or plan_runtime < plan_info_dict[sql]['opt_runtime']:
                    plan_info_dict[sql]['opt_runtime'] = plan_runtime
                    plan_info_dict[sql]['opt_plan'] = analyze_plan
                    plan_info_dict[sql]['opt_hint'] = hint
            else:
                # exhaustive plan
                if plan_info_dict[sql]['opt_runtime'] is None or plan_runtime < plan_info_dict[sql]['opt_runtime']:
                    plan_info_dict[sql]['opt_runtime'] = plan_runtime
                    plan_info_dict[sql]['opt_plan'] = analyze_plan
                    plan_info_dict[sql]['opt_hint'] = hint

            # assert hint not in plan_info_dict[sql]['hint_plan_dict']
            plan_info_dict[sql]['hint_plan_dict'][hint] = analyze_plan

            # count number of tables
            def get_leaf_nodes(plan) -> List[Dict]:
                if 'Plans' not in plan or len(plan['Plans']) == 0:
                    return [plan]
                else:
                    tmp_list = []
                    for child in plan['Plans']:
                        tmp_list.extend(get_leaf_nodes(child))
                    return tmp_list

            # get leaf nodes
            leaf_nodes = get_leaf_nodes(analyze_plan['Plan'])

            if plan_info_dict[sql]['num_tables'] is None:
                plan_info_dict[sql]['num_tables'] = len(leaf_nodes)
            else:
                assert plan_info_dict[sql]['num_tables'] == len(
                    leaf_nodes), f'Different number of tables: {len(leaf_nodes)} vs. {plan_info_dict[sql]["num_tables"]} for sql: \n{sql} with hint: {hint}'

        for sql_info in plan_info_dict.values():
            annotate_num_plans_and_runtime_per_sql_statistics(sql_info)
            annotate_speedup(sql_info)
            annotate_card_est_info(sql_info)

        benchmark_stats_dict[benchmark_name] = plan_info_dict

    if return_run_files:
        return benchmark_stats_dict, run_files
    else:
        return benchmark_stats_dict
