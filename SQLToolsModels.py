VERSION = "v0.0.1"

import re
import os
import sys
import json

dirpath = os.path.dirname(__file__)
if dirpath not in sys.path:
    sys.path.append(dirpath)

from cudatext import *

import threading
import signal
import shlex
import subprocess
import shutil
import sqlparse


class SettingsManager:
    @staticmethod
    def default(file):
        return os.path.join(os.path.dirname(__file__), file)

    @staticmethod
    def file(file):
        path = os.path.join(app_path(APP_DIR_SETTINGS), file)
        if not os.path.isfile(path):
            shutil.copy(SettingsManager.default(file), path)
        return path

    @staticmethod
    def asJson(file):
        return Utils.parseJson(SettingsManager.file(file))


class Settings:

    @staticmethod
    def get(key, default=None):
        keys = key.split('.')
        settings = SettingsManager.asJson(Const.SETTINGS_FILENAME)
        value = settings
        for key in keys:
            value = value.get(key, None)

        return value

    @staticmethod
    def getConnections():
        connections = {}
        options = SettingsManager.asJson(Const.CONNECTIONS_FILENAME)
        options = options.get('connections')

        for connection in options:
            connections[connection] = Connection(
                connection, options[connection])

        return connections


class Const:
    SETTINGS_EXTENSION = "json"
    SETTINGS_FILENAME = "cuda_sqltools_settings.{0}".format(
        SETTINGS_EXTENSION)
    CONNECTIONS_FILENAME = "cuda_sqltools_connections.{0}".format(
        SETTINGS_EXTENSION)
    USER_QUERIES_FILENAME = "cuda_sqltools_savedqueries.{0}".format(
        SETTINGS_EXTENSION)


class Log:

    @staticmethod
    def debug(message):
        if not Settings.get('debug', False):
            return
        print ("SQLTools %s: %s" % (VERSION, message))


class Connection:

    def __init__(self, name, options):

        self.cli = Settings.get('cli')[
            options['type']]
        cli_path = shutil.which(self.cli)

        if cli_path is None:
            msg_box((
                "'{0}' could not be found by CudaText.\n\n" +
                "Please set the '{0}' path in your SQLTools settings " +
                "before continue.").format(self.cli), MB_OK + MB_ICONWARNING)
            return

        self.rowsLimit = SettingsManager.asJson(
            Const.SETTINGS_FILENAME).get('show_records.limit', 50)
        self.options = options
        self.name = name
        self.type = options['type']
        self.host = options['host']
        self.port = options['port']
        self.username = options['username']
        self.database = options['database']

        if 'encoding' in options:
            self.encoding = options['encoding']

        if 'password' in options:
            self.password = options['password']

        if 'service' in options:
            self.service = options['service']

    def __str__(self):
        return self.name

    def _info(self):
        return 'DB: {0}, Connection: {1}@{2}:{3}'.format(
            self.database, self.username, self.host, self.port)

    def toQuickPanel(self):
        return [self.name, self._info()]

    @staticmethod
    def loadDefaultConnectionName():
        default = SettingsManager.asJson(
            Const.CONNECTIONS_FILENAME).get('default', False)
        if not default:
            return
        Log.debug('Default database set to ' + default +
                  '. Loading options and auto complete.')
        return default

    def getTables(self, callback):
        query = self.getOptionsForSgdbCli()['queries']['desc']['query']

        def cb(result):
            return Utils.getResultAsList(result, callback)

        Command.createAndRun(self.builArgs('desc'), query, cb)

    def getColumns(self, callback):

        def cb(result):
            return Utils.getResultAsList(result, callback)

        try:
            query = self.getOptionsForSgdbCli()['queries']['columns']['query']
            Command.createAndRun(self.builArgs('columns'), query, cb)
        except Exception:
            pass

    def getFunctions(self, callback):

        def cb(result):
            return Utils.getResultAsList(result, callback)

        try:
            query = self.getOptionsForSgdbCli()['queries'][
                'functions']['query']
            Command.createAndRun(self.builArgs(
                'functions'), query, cb)
        except Exception:
            pass

    def getTableRecords(self, tableName, callback):
        query = self.getOptionsForSgdbCli()['queries']['show records'][
            'query'].format(tableName, self.rowsLimit)
        Command.createAndRun(self.builArgs('show records'), query, callback)

    def getTableDescription(self, tableName, callback):
        query = self.getOptionsForSgdbCli()['queries']['desc table'][
            'query'] % tableName
        Command.createAndRun(self.builArgs('desc table'), query, callback)

    def getFunctionDescription(self, functionName, callback):
        query = self.getOptionsForSgdbCli()['queries']['desc function'][
            'query'] % functionName
        Command.createAndRun(self.builArgs('desc function'), query, callback)

    def execute(self, queries, callback):
        queryToRun = ''

        for query in self.getOptionsForSgdbCli()['before']:
            queryToRun += query + "\n"

        if isinstance(queries, str):
            queries = [queries]

        for query in queries:
            queryToRun += query + "\n"

        queryToRun = queryToRun.rstrip('\n')

        Log.debug("Query: " + queryToRun)
        History.add(queryToRun)
        Command.createAndRun(self.builArgs(), queryToRun, callback)

    def builArgs(self, queryName=None):
        cliOptions = self.getOptionsForSgdbCli()
        args = [self.cli]

        if len(cliOptions['options']) > 0:
            args = args + cliOptions['options']

        if queryName and len(cliOptions['queries'][queryName]['options']) > 0:
            args = args + cliOptions['queries'][queryName]['options']

        if isinstance(cliOptions['args'], list):
            cliOptions['args'] = ' '.join(cliOptions['args'])

        cliOptions = cliOptions['args'].format(**self.options)
        args = args + shlex.split(cliOptions)

        Log.debug('Usgin cli args ' + ' '.join(args))
        return args

    def getOptionsForSgdbCli(self):
        return Settings.get('cli_options')[self.type]


class Selection:
    @staticmethod
    def get():
        selection = ed.get_text_sel()
        return selection if selection and selection != "" else None

    @staticmethod
    def formatSql():
        text = Selection.get()
        x0, y0, x1, y1 = ed.get_sel_rect()
        ed.delete(x0, y0, x1, y1)

        ed.insert(x0, y0, Utils.formatSql(text))


class Command:
    def __init__(self, args, callback, query=None, encoding='utf-8'):
        self.query = query
        self.process = None
        self.args = args
        self.encoding = encoding
        self.callback = callback

    def start(self):
        if not self.query:
            return
        msg_status('ST: running SQL command')
        self.args = map(str, self.args)
        si = None
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        self.process = subprocess.Popen(self.args,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        stdin=subprocess.PIPE,
                                        env=os.environ.copy(),
                                        startupinfo=si)
        results, errors = self.process.communicate(input=self.query.encode())

        resultString = ''

        if results:
            resultString += results.decode(self.encoding,
                                           'replace').replace('\r', '')

        if errors:
            resultString += errors.decode(self.encoding,
                                          'replace').replace('\r', '')

        self.callback(resultString)

    @staticmethod
    def createAndRun(args, query, callback):
        command = Command(args, callback, query)
        command.start()


class Utils:
    # Regular expression for comments
    comment_re = re.compile(
        '(^)?[^\S\n]*/(?:\*(.*?)\*/[^\S\n]*|/[^\n]*)($)?',
        re.DOTALL | re.MULTILINE
    )

    @staticmethod
    def parseJson(filename):
        """ Parse a JSON file
            First remove comments and then use the json module package
            Comments look like :
                // ...
            or
                /*
                ...
                */
        """

        with open(filename) as f:
            content = ''.join(f.readlines())

            # Looking for comments
            match = Utils.comment_re.search(content)
            while match:
                # single line comment
                content = content[:match.start()] + content[match.end():]
                match = Utils.comment_re.search(content)

            # Return json file
            return json.loads(content)

    @staticmethod
    def getResultAsList(results, callback=None):
        resultList = []
        for result in results.splitlines():
            try:
                resultList.append(result.split('|')[1].strip())
            except IndexError:
                pass

        if callback:
            callback(resultList)

        return resultList

    @staticmethod
    def formatSql(raw):
        settings = Settings.get("format")
        try:
            result = sqlparse.format(raw,
                                     keyword_case=settings.get("keyword_case"),
                                     identifier_case=settings.get(
                                         "identifier_case"),
                                     strip_comments=settings.get(
                                         "strip_comments"),
                                     indent_tabs=settings.get("indent_tabs"),
                                     indent_width=settings.get("indent_width"),
                                     reindent=settings.get("reindent")
                                     )

            return result
        except Exception:
            return None


class History:
    queries = []

    @staticmethod
    def add(query):
        if len(History.queries) >= Settings.get('history_size', 100):
            History.queries.pop(0)
        History.queries.insert(0, query)

    @staticmethod
    def get(index):
        if index < 0 or index > (len(History.queries) - 1):
            raise "No query selected"

        return History.queries[index]
