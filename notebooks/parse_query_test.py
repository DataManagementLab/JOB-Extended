import unittest

import sqlparse
from analysis.JOBExtended.parse_query import get_query_tables, get_preds_from_query, parse_join_conditions
from sqlparse.sql import Identifier, Comparison


class ParseQueryTest(unittest.TestCase):
    def test_get_preds_from_query(self):
        query = '''SELECT MIN(chn.name) AS voiced_char, MIN(n.name) AS voicing_actress, MIN(t.title) AS voiced_animation
                   FROM aka_name AS an,
                        complete_cast AS cc,
                        comp_cast_type AS cct1,
                        comp_cast_type AS cct2,
                        char_name AS chn,
                        cast_info AS ci,
                        company_name AS cn,
                        info_type AS it,
                        info_type AS it3,
                        keyword AS k,
                        movie_companies AS mc,
                        movie_info AS mi,
                        movie_keyword AS mk,
                        name AS n,
                        person_info AS pi,
                        role_type AS rt,
                        title AS t
                   WHERE cct1.kind = 'cast'
                     AND cct2.kind = 'complete+verified'
                     AND chn.name = 'Queen'
                     AND ci.note IN ('(voice)', '(voice) (uncredited)', '(voice: English version)')
                     AND cn.country_code = '[us]'
                     AND it.info = 'release dates'
                     AND it3.info = 'height'
                     AND k.keyword = 'computer-animation'
                     AND mi.info LIKE 'USA:%200%'
                     AND n.gender = 'f'
                     AND n.name LIKE '%An%'
                     AND rt.role = 'actress'
                     AND t.title = 'Shrek 2'
                     AND t.production_year BETWEEN 2000 AND 2005
                     AND t.id = mi.movie_id;'''
        parsed_query = sqlparse.parse(query)

        join_tables, table_alias_dict = get_query_tables(parsed_query)
        expected_join_tables = ['aka_name',
                                'cast_info',
                                'char_name',
                                'comp_cast_type',
                                'company_name',
                                'complete_cast',
                                'info_type',
                                'keyword',
                                'movie_companies',
                                'movie_info',
                                'movie_keyword',
                                'name',
                                'person_info',
                                'role_type',
                                'title']
        self.assertEqual(expected_join_tables, join_tables)

        expected_alias_dict = {
            'an': 'aka_name',
            'cc': 'complete_cast',
            'cct1': 'comp_cast_type',
            'cct2': 'comp_cast_type',
            'chn': 'char_name',
            'ci': 'cast_info',
            'cn': 'company_name',
            'it': 'info_type',
            'it3': 'info_type',
            'k': 'keyword',
            'mc': 'movie_companies',
            'mi': 'movie_info',
            'mk': 'movie_keyword',
            'n': 'name',
            'pi': 'person_info',
            'rt': 'role_type',
            't': 'title'
        }
        self.assertEqual(expected_alias_dict, table_alias_dict)

        predicates, num_disj1 = get_preds_from_query(parsed_query)

        self.assertEqual(15, len(predicates))
        self.assertEqual(num_disj1, 0)

        query2 = 'SELECT COUNT(*) FROM t1 WHERE t1.col IS NOT NULL;'
        parsed_query2 = sqlparse.parse(query2)
        join_tables2, alias_dict = get_query_tables(parsed_query2)
        expected_join_tables2 = ['t1']
        self.assertEqual(expected_join_tables2, join_tables2)
        self.assertEqual({'t1': 't1'}, alias_dict)
        predicates2, num_disj2 = get_preds_from_query(parsed_query2)
        self.assertEqual(1, len(predicates2))
        self.assertEqual(num_disj2, 0)

        or_query = 'SELECT COUNT(*) FROM t1 WHERE t1.col IS NOT NULL OR t1.col2 IS NOT NULL;'
        parsed_or_query = sqlparse.parse(or_query)
        predicates3, num_disj3 = get_preds_from_query(parsed_or_query)
        self.assertEqual(2, len(predicates3))
        self.assertEqual(num_disj3, 1)

        query = '''SELECT MIN(a1.name) AS writer_pseudo_name, MIN(t.title) AS movie_title
                   FROM aka_name AS a1,
                        cast_info AS ci,
                        company_name AS cn,
                        movie_companies AS mc,
                        name AS n1,
                        role_type AS rt,
                        title AS t,
                        char_name AS cn2
                   WHERE cn.country_code = '[us]'
                     AND rt.role = 'writer'
                     AND a1.person_id = n1.id
                     AND n1.id = ci.person_id
                     AND ci.movie_id = t.id
                     AND t.id = mc.movie_id
                     AND mc.company_id = cn.id
                     AND ci.role_id = rt.id
                     AND a1.person_id = ci.person_id
                     AND ci.movie_id = mc.movie_id
                     AND cn2.imdb_index = t.imdb_index
                     AND (ci.nr_order = t.season_nr OR ci.nr_order IS NULL)
                     AND (ci.person_role_id = t.season_nr OR ci.person_role_id IS NULL);'''
        parsed_query = sqlparse.parse(query)
        predicates, num_disj1 = get_preds_from_query(parsed_query)
        self.assertEqual(15, len(predicates))
        self.assertEqual(num_disj1, 2)

        query = '''SELECT MIN(a1.name) AS writer_pseudo_name, MIN(t.title) AS movie_title \
                   FROM cast_info AS ci
                   WHERE (ci.nr_order = t.season_nr OR ci.nr_order IS NULL) \
                     AND (ci.person_role_id = t.season_nr OR ci.person_role_id IS NULL);'''
        parsed_query = sqlparse.parse(query)
        predicates, num_disj1 = get_preds_from_query(parsed_query)
        self.assertEqual(4, len(predicates))
        self.assertEqual(num_disj1, 2)

    def test_parse_join_conditions(self):
        query = '''SELECT MIN(chn.name) AS voiced_char, MIN(n.name) AS voicing_actress, MIN(t.title) AS voiced_animation
                   FROM aka_name AS an,
                        complete_cast AS cc,
                        comp_cast_type AS cct1,
                        comp_cast_type AS cct2,
                        char_name AS chn,
                        cast_info AS ci,
                        company_name AS cn,
                        info_type AS it,
                        info_type AS it3,
                        keyword AS k,
                        movie_companies AS mc,
                        movie_info AS mi,
                        movie_keyword AS mk,
                        name AS n,
                        person_info AS pi,
                        role_type AS rt,
                        title AS t
                   WHERE cct1.kind = 'cast'
                     AND cct2.kind = 'complete+verified'
                     AND chn.name = 'Queen'
                     AND ci.note IN ('(voice)', '(voice) (uncredited)', '(voice: English version)')
                     AND cn.country_code = '[us]'
                     AND it.info = 'release dates'
                     AND it3.info = 'height'
                     AND k.keyword = 'computer-animation'
                     AND mi.info LIKE 'USA:%200%'
                     AND n.gender = 'f'
                     AND n.name LIKE '%An%'
                     AND rt.role = 'actress'
                     AND t.title = 'Shrek 2'
                     AND t.production_year BETWEEN 2000 AND 2005
                     AND t.id = mi.movie_id;'''
        parsed_query = sqlparse.parse(query)
        predicates, _ = get_preds_from_query(parsed_query)
        _, table_alias_dict = get_query_tables(parsed_query)

        # filter out join conditions
        join_conds = [p for p in predicates if
                      isinstance(p, Comparison) and isinstance(p.left, Identifier) and isinstance(p.right, Identifier)]
        parsed_join_conds = parse_join_conditions(join_conds, table_alias_dict)

        expected_join_conds = [('title', 'id', 'movie_info', 'movie_id')]
        self.assertEqual(expected_join_conds, parsed_join_conds)


if __name__ == '__main__':
    unittest.main()
