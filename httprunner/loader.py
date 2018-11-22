import collections
import copy
import csv
import importlib
import io
import json
import os
import sys

import yaml
from httprunner import exceptions, logger, parser, utils, validator
from httprunner.compat import OrderedDict

###############################################################################
##   file loader
###############################################################################

def _check_format(file_path, content):
    """ check testcase format if valid
    """
    # TODO: replace with JSON schema validation
    if not content:
        # testcase file content is empty
        err_msg = u"Testcase file content is empty: {}".format(file_path)
        logger.log_error(err_msg)
        raise exceptions.FileFormatError(err_msg)

    elif not isinstance(content, (list, dict)):
        # testcase file content does not match testcase format
        err_msg = u"Testcase file content format invalid: {}".format(file_path)
        logger.log_error(err_msg)
        raise exceptions.FileFormatError(err_msg)


def load_yaml_file(yaml_file):
    """ load yaml file and check file content format
    """
    with io.open(yaml_file, 'r', encoding='utf-8') as stream:
        yaml_content = yaml.load(stream)
        _check_format(yaml_file, yaml_content)
        return yaml_content


def load_json_file(json_file):
    """ load json file and check file content format
    """
    with io.open(json_file, encoding='utf-8') as data_file:
        try:
            json_content = json.load(data_file)
        except exceptions.JSONDecodeError:
            err_msg = u"JSONDecodeError: JSON file format error: {}".format(json_file)
            logger.log_error(err_msg)
            raise exceptions.FileFormatError(err_msg)

        _check_format(json_file, json_content)
        return json_content


def load_csv_file(csv_file):
    """ load csv file and check file content format
    @param
        csv_file: csv file path
        e.g. csv file content:
            username,password
            test1,111111
            test2,222222
            test3,333333
    @return
        list of parameter, each parameter is in dict format
        e.g.
        [
            {'username': 'test1', 'password': '111111'},
            {'username': 'test2', 'password': '222222'},
            {'username': 'test3', 'password': '333333'}
        ]
    """
    csv_content_list = []

    with io.open(csv_file, encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            csv_content_list.append(row)

    return csv_content_list


def load_file(file_path):
    if not os.path.isfile(file_path):
        raise exceptions.FileNotFound("{} does not exist.".format(file_path))

    file_suffix = os.path.splitext(file_path)[1].lower()
    if file_suffix == '.json':
        return load_json_file(file_path)
    elif file_suffix in ['.yaml', '.yml']:
        return load_yaml_file(file_path)
    elif file_suffix == ".csv":
        return load_csv_file(file_path)
    else:
        # '' or other suffix
        err_msg = u"Unsupported file format: {}".format(file_path)
        logger.log_warning(err_msg)
        return []


def load_folder_files(folder_path, recursive=True):
    """ load folder path, return all files endswith yml/yaml/json in list.

    Args:
        folder_path (str): specified folder path to load
        recursive (bool): load files recursively if True

    Returns:
        list: files endswith yml/yaml/json
    """
    if isinstance(folder_path, (list, set)):
        files = []
        for path in set(folder_path):
            files.extend(load_folder_files(path, recursive))

        return files

    if not os.path.exists(folder_path):
        return []

    file_list = []

    for dirpath, dirnames, filenames in os.walk(folder_path):
        filenames_list = []

        for filename in filenames:
            if not filename.endswith(('.yml', '.yaml', '.json')):
                continue

            filenames_list.append(filename)

        for filename in filenames_list:
            file_path = os.path.join(dirpath, filename)
            file_list.append(file_path)

        if not recursive:
            break

    return file_list


def load_dot_env_file(dot_env_path):
    """ load .env file.

    Args:
        dot_env_path (str): .env file path

    Returns:
        dict: environment variables mapping

            {
                "UserName": "debugtalk",
                "Password": "123456",
                "PROJECT_KEY": "ABCDEFGH"
            }

    Raises:
        exceptions.FileFormatError: If .env file format is invalid.

    """
    if not os.path.isfile(dot_env_path):
        return {}

    logger.log_info("Loading environment variables from {}".format(dot_env_path))
    env_variables_mapping = {}

    with io.open(dot_env_path, 'r', encoding='utf-8') as fp:
        for line in fp:
            # maxsplit=1
            if "=" in line:
                variable, value = line.split("=", 1)
            elif ":" in line:
                variable, value = line.split(":", 1)
            else:
                raise exceptions.FileFormatError(".env format error")

            env_variables_mapping[variable.strip()] = value.strip()

    utils.set_os_environ(env_variables_mapping)
    return env_variables_mapping


def locate_file(start_path, file_name):
    """ locate filename and return absolute file path.
        searching will be recursive upward until current working directory.

    Args:
        start_path (str): start locating path, maybe file path or directory path

    Returns:
        str: located file path. None if file not found.

    Raises:
        exceptions.FileNotFound: If failed to locate file.

    """
    if os.path.isfile(start_path):
        start_dir_path = os.path.dirname(start_path)
    elif os.path.isdir(start_path):
        start_dir_path = start_path
    else:
        raise exceptions.FileNotFound("invalid path: {}".format(start_path))

    file_path = os.path.join(start_dir_path, file_name)
    if os.path.isfile(file_path):
        return os.path.abspath(file_path)

    # current working directory
    if os.path.abspath(start_dir_path) in [os.getcwd(), os.path.abspath(os.sep)]:
        raise exceptions.FileNotFound("{} not found in {}".format(file_name, start_path))

    # locate recursive upward
    return locate_file(os.path.dirname(start_dir_path), file_name)


###############################################################################
##   debugtalk.py module loader
###############################################################################

def load_module_functions(module):
    """ load python module functions.

    Args:
        module: python module

    Returns:
        dict: functions mapping for specified python module

            {
                "func1_name": func1,
                "func2_name": func2
            }

    """
    module_functions = {}

    for name, item in vars(module).items():
        if validator.is_function(item):
            module_functions[name] = item

    return module_functions


def load_builtin_functions():
    """ load built_in module functions
    """
    from httprunner import built_in
    return load_module_functions(built_in)


def load_debugtalk_functions():
    """ load project debugtalk.py module functions
        debugtalk.py should be located in project working directory.

    Returns:
        dict: debugtalk module functions mapping
            {
                "func1_name": func1,
                "func2_name": func2
            }

    """
    # load debugtalk.py module
    imported_module = importlib.import_module("debugtalk")
    return load_module_functions(imported_module)


###############################################################################
##   testcase loader
###############################################################################

project_mapping = {}
tests_def_mapping = {
    "api": {},
    "testcases": {}
}

def load_teststep(raw_stepinfo):
    """ load teststep with api/testcase/proc references

    Args:
        raw_stepinfo (dict): teststep data, maybe in 3 formats.
            # api reference
            {
                "name": "add product to cart",
                "api": "api_add_cart",
                "variables": [],
                "validate": [],
                "extract": []
            }
            # testcase reference
            {
                "name": "add product to cart",
                "testcase": "create_and_check",
                "variables": []
            }
            # define directly
            {
                "name": "checkout cart",
                "request": {},
                "variables": [],
                "validate": [],
                "extract": []
            }

    Returns:
        list: loaded teststeps list

    Args:
        raw_stepinfo (dict): teststep info

    """
    # reference api
    if "api" in raw_stepinfo:
        api_name = raw_stepinfo["api"]
        raw_stepinfo["api_def"] = _get_api_definition(api_name)

    # TODO: reference proc functions
    elif "func" in raw_stepinfo:
        pass

    # reference testcase
    elif "testcase" in raw_stepinfo:
        testcase_path = raw_stepinfo["testcase"]

        if testcase_path not in tests_def_mapping["testcases"]:
            testcase_path = os.path.join(
                project_mapping["PWD"],
                testcase_path
            )
            testcase_dict = load_testcase(load_file(testcase_path))
            tests_def_mapping[testcase_path] = testcase_dict
        else:
            testcase_dict = tests_def_mapping[testcase_path]

        raw_stepinfo["testcase_def"] = testcase_dict

    # define directly
    else:
        pass

    return raw_stepinfo


def load_testcase(raw_testcase):
    """ load testcase/testsuite with api/testcase references

    Args:
        raw_testcase (list): raw testcase content loaded from JSON/YAML file:
            [
                # config part
                {
                    "config": {
                        "name": "",
                        "def": "suite_order()",
                        "request": {}
                    }
                },
                # teststeps part
                {
                    "test": {...}
                },
                {
                    "test": {...}
                }
            ]

    Returns:
        dict: loaded testcase content
            {
                "name": "XYZ",
                "config": {},
                "teststeps": [teststep11, teststep12]
            }

    """
    config = {}
    teststeps = []

    for item in raw_testcase:
        # TODO: add json schema validation
        if not isinstance(item, dict) or len(item) != 1:
            raise exceptions.FileFormatError("Testcase format error: {}".format(item))

        key, test_block = item.popitem()
        if not isinstance(test_block, dict):
            raise exceptions.FileFormatError("Testcase format error: {}".format(item))

        if key == "config":
            config.update(test_block)

        elif key == "test":
            teststeps.append(load_teststep(test_block))

        else:
            logger.log_warning(
                "unexpected block key: {}. block key should only be 'config' or 'test'.".format(key)
            )

    return {
        "config": config,
        "teststeps": teststeps
    }


def _get_api_definition(name):
    """ get api definition by name.

    Returns:
        dict: expected api definition if found.

    Raises:
        exceptions.ApiNotFound: api not found

    """
    try:
        block = tests_def_mapping["api"][name]
        # NOTICE: avoid project_mapping been changed during iteration.
        return utils.deepcopy_dict(block)
    except KeyError:
        raise exceptions.ApiNotFound("{} not found!".format(name))


def load_folder_content(folder_path):
    """ load api/testcases/testsuites definitions from folder.

    Args:
        folder_path (str): api/testcases/testsuites files folder.

    Returns:
        dict: api definition mapping.

            {
                "tests/api/basic.yml": [
                    {"api": {"def": "api_login", "request": {}, "validate": []}},
                    {"api": {"def": "api_logout", "request": {}, "validate": []}}
                ]
            }

    """
    items_mapping = {}

    for file_path in load_folder_files(folder_path):
        items_mapping[file_path] = load_file(file_path)

    return items_mapping


def load_api_folder(api_folder_path):
    """ load api definitions from api folder.

    Args:
        api_folder_path (str): api files folder.

            api file should be in the following format:
            [
                {
                    "api": {
                        "def": "api_login",
                        "request": {},
                        "validate": []
                    }
                },
                {
                    "api": {
                        "def": "api_logout",
                        "request": {},
                        "validate": []
                    }
                }
            ]

    Returns:
        dict: api definition mapping.

            {
                "api_login": {
                    "function_meta": {"func_name": "api_login", "args": [], "kwargs": {}}
                    "request": {}
                },
                "api_logout": {
                    "function_meta": {"func_name": "api_logout", "args": [], "kwargs": {}}
                    "request": {}
                }
            }

    """
    # TODO: refactor api storage format, use one file for each api.
    api_definition_mapping = {}

    api_items_mapping = load_folder_content(api_folder_path)

    for api_file_path, api_items in api_items_mapping.items():
        # TODO: add JSON schema validation
        for api_item in api_items:
            key, api_dict = api_item.popitem()

            # TODO: replace id with api file path
            api_id = api_dict.get("id")
            if api_id in api_definition_mapping:
                logger.log_warning("API definition duplicated: {}".format(api_id))

            api_definition_mapping[api_id] = api_dict

    return api_definition_mapping


def load_debugtalk_py(start_path):
    """ locate debugtalk.py file and returns PWD and debugtalk.py functions.

    Args:
        start_path (str): start locating path, maybe testcase file path or directory path

    Returns:
        tuple: (project_working_directory, debugtalk_functions)

    """
    try:
        # locate debugtalk.py file.
        debugtalk_path = locate_file(start_path, "debugtalk.py")

        # The folder contains debugtalk.py will be treated as PWD.
        project_working_directory = os.path.dirname(debugtalk_path)

        # add PWD to sys.path
        sys.path.insert(0, project_working_directory)

        # load debugtalk.py functions
        debugtalk_functions = load_debugtalk_functions()

    except exceptions.FileNotFound:

        # debugtalk.py not found, use os.getcwd() as PWD.
        project_working_directory = os.getcwd()

        # add PWD to sys.path
        sys.path.insert(0, project_working_directory)

        debugtalk_functions = {}

    return project_working_directory, debugtalk_functions


def load_project_tests(test_path, dot_env_path=None):
    """ load api, testcases, .env, debugtalk.py functions.
        api/testcases folder is relative to project_working_directory

    Args:
        test_path (str): test file/folder path, locate pwd from this path.
        dot_env_path (str): specified .env file path

    Returns:
        dict: project loaded api/testcases definitions, environments and debugtalk.py functions.

    """
    # locate PWD and load debugtalk.py functions
    project_working_directory, debugtalk_functions = load_debugtalk_py(test_path)
    project_mapping["PWD"] = project_working_directory
    project_mapping["functions"] = debugtalk_functions

    # load .env
    dot_env_path = dot_env_path or os.path.join(project_working_directory, ".env")
    project_mapping["env"] = load_dot_env_file(dot_env_path)

    # load api
    tests_def_mapping["api"] = load_api_folder(os.path.join(project_working_directory, "api"))


def load_tests(path, dot_env_path=None):
    """ load testcases from file path, extend and merge with api/testcase definitions.

    Args:
        path (str/list): testcase file/foler path.
            path could be in 2 types:
                - absolute/relative file path
                - absolute/relative folder path
        dot_env_path (str): specified .env file path

    Returns:
        dict: tests mapping, include project_mapping and testcases.
              each testcase is corresponding to a file.
            {
                "project_mapping": {
                    "PWD": "XXXXX",
                    "functions": {},
                    "env": {}
                },
                "testcases": [
                    {   # testcase data structure
                        "config": {
                            "name": "desc1",
                            "path": "testcase1_path",
                            "variables": [],                    # optional
                        },
                        "teststeps": [
                            # teststep data structure
                            {
                                'name': 'test step desc1',
                                'variables': [],    # optional
                                'extract': [],      # optional
                                'validate': [],
                                'request': {}
                            },
                            teststep2   # another teststep dict
                        ]
                    },
                    testcase_dict_2     # another testcase dict
                ]
            }

    """
    if not os.path.exists(path):
        err_msg = "path not exist: {}".format(path)
        logger.log_error(err_msg)
        raise exceptions.FileNotFound(err_msg)

    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)

    load_project_tests(path, dot_env_path)
    tests_mapping = {
        "project_mapping": project_mapping
    }

    def load_test_file(path):
        raw_testcase = load_file(path)

        try:
            testcase = load_testcase(raw_testcase)
            testcase["config"]["path"] = path
        except exceptions.FileFormatError:
            testcase = {}

        return testcase

    testcases_list = []

    if os.path.isdir(path):
        files_list = load_folder_files(path)
        for path in files_list:
            testcase = load_test_file(path)
            if not testcase:
                continue
            testcases_list.append(testcase)

    elif os.path.isfile(path):

        testcase = load_test_file(path)
        if testcase:
            testcases_list.append(testcase)

    tests_mapping["testcases"] = testcases_list

    return tests_mapping


def load_locust_tests(path, dot_env_path=None):
    """ load locust testcases

    Args:
        path (str): testcase/testsuite file path.
        dot_env_path (str): specified .env file path

    Returns:
        dict: locust testcases with weight
        {
            "config": {...},
            "tests": [
                # weight 3
                [teststep11],
                [teststep11],
                [teststep11],
                # weight 2
                [teststep21, teststep22],
                [teststep21, teststep22]
            ]
        }

    """
    raw_testcase = load_file(path)
    load_project_tests(path, dot_env_path)

    config = {}
    tests = []
    for item in raw_testcase:
        key, test_block = item.popitem()

        if key == "config":
            config.update(test_block)
        elif key == "test":
            teststep = load_teststep(test_block)
            weight = test_block.get("weight", 1)
            for _ in range(weight):
                tests.append(teststep)

    # parse config variables
    raw_config_variables = config.get("variables", [])

    config_variables = parser.parse_data(
        raw_config_variables,
        {},
        project_mapping["functions"]
    )

    # parse config name
    config["name"] = parser.parse_data(
        config.get("name", ""),
        config_variables,
        project_mapping["functions"]
    )

    # parse config request
    config["request"] = parser.parse_data(
        config.get("request", {}),
        config_variables,
        project_mapping["functions"]
    )

    return {
        "config": config,
        "tests": tests
    }
