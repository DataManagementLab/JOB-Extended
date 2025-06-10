from collections import defaultdict
from typing import Tuple, Set, List, Dict

from sqlparse.sql import Function, Where, Comparison, IdentifierList, Statement, Token, Identifier, \
    Parenthesis


def get_query_tables(query: Tuple[Statement], query_tables: Set[str] = None, alias_table_dict: Dict[str, str] = None,
                     depth: int = 0) -> Tuple[Set[str], Dict[str, str]]:
    """Given a query, this method will extract the unique set of join tables recursively"""
    if query_tables is None:
        query_tables = set()
        alias_table_dict = dict()

    # skip until FROM clause
    tokens = list(query)
    if tokens[0].value.upper() == 'SELECT':
        # skip until from
        for i in range(len(tokens)):
            if tokens[i].value.upper() == 'FROM':
                tokens = tokens[i:]
                break

    for token in tokens:
        if not isinstance(token, (Function, Where, Comparison)):
            if isinstance(token, IdentifierList):
                get_query_tables(token.tokens, query_tables, alias_table_dict=alias_table_dict, depth=depth + 1)
            elif isinstance(token, Identifier):
                table_name = token.normalized.replace('"', '')
                query_tables.add(table_name)
                alias = token.get_alias()
                if alias is not None:
                    alias_table_dict[alias] = table_name
                else:
                    alias_table_dict[table_name] = table_name

            elif hasattr(token, "tokens"):
                get_query_tables(token.tokens, query_tables, alias_table_dict=alias_table_dict, depth=depth + 1)
    if depth == 0:
        return [t for t in sorted(query_tables)], alias_table_dict
    else:
        return query_tables, alias_table_dict


def parse_token_list(token_list: List) -> List:
    tmp_stack = []
    token_idx = 0
    comparisons = []
    num_disjunctions = 0

    # strip whitespace from token list
    token_list = [t for t in token_list if not (isinstance(t, Token) and t.value.strip() == '')]

    while token_idx < len(token_list):
        subtoken = token_list[token_idx]

        if type(subtoken) == Comparison:
            comparisons.append(subtoken)
        elif type(subtoken) == Token and subtoken.value.strip() in ['', 'AND', 'WHERE', ';', '(', ')']:
            pass
        elif type(subtoken) == Token and subtoken.value.strip() == 'OR':
            num_disjunctions += 1
        elif type(subtoken) == Token and subtoken.value.strip() == 'IN':
            # next token should include the list of vals
            next_token = token_list[token_idx + 1]
            assert isinstance(next_token, Parenthesis), f'Expected Parenthesis, got {type(next_token)} / {next_token}'

            assert len(tmp_stack) == 1, f'Expected 1 token in stack, got {len(tmp_stack)} / {tmp_stack}'
            prev_token = tmp_stack.pop()
            assert isinstance(prev_token, Identifier), f'Expected Identifier, got {type(prev_token)} / {prev_token}'

            comparisons.append(('IN', prev_token, subtoken, next_token))

            # skip next token
            token_idx += 1
        elif type(subtoken) == Token and subtoken.value.strip() == 'BETWEEN':
            # previous token must be on the stack and be column
            assert len(tmp_stack) == 1, f'Expected 1 token in stack, got {len(tmp_stack)} / {tmp_stack}'
            prev_token = tmp_stack.pop()
            assert isinstance(prev_token, Identifier), f'Expected Identifier, got {type(prev_token)} / {prev_token}'

            # next token should be INT
            from_range = token_list[token_idx + 1]
            assert isinstance(from_range, Token), f'Expected Token, got {type(from_range)} / {from_range}'

            # 2nd next token should be AND
            assert token_list[token_idx + 2].value.strip() == 'AND', f'Expected AND, got {token_list[token_idx + 2]}'

            # 3rd next token should be INT
            to_range = token_list[token_idx + 3]
            assert isinstance(to_range, Token), f'Expected Token, got {type(to_range)} / {to_range}'

            comparisons.append(('BETWEEN', prev_token, subtoken, from_range, to_range))

            # skip 3 tokens
            token_idx += 3

        elif type(subtoken) == Token and subtoken.value.strip() == 'IS':
            # previous token must be on the stack and be column
            assert len(tmp_stack) == 1, f'Expected 1 token in stack, got {len(tmp_stack)} / {tmp_stack}'
            prev_token = tmp_stack.pop()
            assert isinstance(prev_token, Identifier), f'Expected Identifier, got {type(prev_token)} / {prev_token}'

            # next token should be NULL or NOT
            next_token = token_list[token_idx + 1]
            assert isinstance(next_token, Token), f'Expected Token, got {type(next_token)} / {next_token}'
            assert next_token.normalized in ['NOT NULL', 'NULL'], f'Expected NULL or NOT NULL, got {next_token}'

            comparisons.append(('IS', prev_token, subtoken, next_token))

            # skip next token
            token_idx += 1
        elif type(subtoken) == Parenthesis:  # very likely we hit an OR clause / ...
            child_conditions, child_num_disjunctions = parse_token_list(subtoken.tokens)
            comparisons.extend(child_conditions)
            num_disjunctions += child_num_disjunctions
        else:
            tmp_stack.append(subtoken)

        token_idx += 1

    assert len(tmp_stack) == 0, f'Expected empty stack, got {len(tmp_stack)} / {tmp_stack}'

    return comparisons, num_disjunctions


def get_preds_from_query(query: Tuple[Statement], flatten_disjunction: bool = True):
    assert flatten_disjunction, f'We do not support disjunctions yet - hence we flatten them. No way to represent disjunctions currently'

    if isinstance(query, tuple):
        query = query[0]

    assert isinstance(query, Statement)

    comparisons = []
    num_disjunctions = 0
    for token in query:
        if isinstance(token, Where):
            # strip whitespace from token list
            token_list = [t for t in token.tokens if not (isinstance(t, Token) and t.value.strip() == '')]
            comps, disj_ctr = parse_token_list(token_list)
            comparisons.extend(comps)
            num_disjunctions += disj_ctr

    return comparisons, num_disjunctions


def extract_filter_jon_conditions(predicates: List):
    join_conds = []
    filter_conds = []
    filter_operators = defaultdict(int)

    filter_cols = []

    for p in predicates:
        if isinstance(p, Comparison):
            if isinstance(p.left, Identifier) and isinstance(p.right, Identifier):
                join_conds.append(p)
            elif isinstance(p.left, Identifier) and isinstance(p.right, Token):
                # comparison with constant
                filter_conds.append(p)

                # retrieve comparison operator
                if str(p[1]) == ' ':
                    op = p[2]
                else:
                    op = p[1]

                assert str(op).strip() != '', f'empty operator {op}'

                filter_operators[str(op)] += 1

                # extract table/column
                filter_cols.append(str(p.left))

            else:
                print(f'{p} / {type(p.left)} / {type(p.right)}')
        elif isinstance(p, tuple):
            # this is a filter condition
            filter_conds.append(p)
            filter_operators[p[0]] += 1

            # extract table/column
            filter_cols.append(str(p[1]))
        else:
            print(f'{p} / {type(p)}')

    return filter_conds, filter_operators, filter_cols, join_conds


def parse_join_conditions(join_conds: List[Comparison], alias_table_dict: Dict[str, str]) -> List[
    Tuple[str, str, str, str]]:
    """
    Parse the join conditions from the SQL query
    :param join_conds: list of join conditions
    :return: list of tuples (table1, column1, table2, column2)
    """
    join_conditions = []
    for token in join_conds:
        assert isinstance(token, Comparison)

        # get the left and right side of the comparison
        left = token.left
        right = token.right

        # check if both sides are identifiers
        assert isinstance(left, Identifier)
        assert isinstance(right, Identifier)

        t1, c1 = left.normalized.split('.')
        t2, c2 = right.normalized.split('.')

        t1 = alias_table_dict[t1]
        t2 = alias_table_dict[t2]

        join_conditions.append((t1, c1, t2, c2))

    return join_conditions
