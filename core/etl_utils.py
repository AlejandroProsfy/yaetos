"""
Helper functions. Setup to run locally and on cluster.
"""
# TODO:
# - add logger
# - extract yml ops to separate class for reuse in Flow()
# - setup command line args defaults to None so they can be overriden only if set in commandline and default would be in config file or jobs_metadata.yml
# - make yml look more like command line info, with path of python script.
# - fix sql job
# - enable sql job as a dependency


import sys
import inspect
import yaml
from datetime import datetime
import os
import boto3
import argparse
from time import time
import StringIO


JOBS_METADATA_FILE = 'conf/jobs_metadata.yml'
JOBS_METADATA_LOCAL_FILE = 'conf/jobs_metadata_local.yml'
CLUSTER_APP_FOLDER = '/home/hadoop/app/'
GIT_REPO = '/Users/aprevot/Documents/Box Sync/code/pyspark_aws_etl/'  # TODO: parametrize


class ETL_Base(object):

    # def commandline_launch(self, **args):
    #     app_name = self.get_job_name()
    #     job_file = self.get_job_file()
    #     commandliner = CommandLiner()
    #     commandliner.commandline_launch(app_name, job_file, args, etl_func=self.etl)

    def etl(self, sc, sc_sql, args, loaded_inputs={}):
        start_time = time()
        self.set_attributes(sc, sc_sql, args)
        print "-------\nStarting running job '{}' in spark app '{}'.".format(self.job_name, self.app_name)

        loaded_datasets = self.load_inputs(loaded_inputs)
        output = self.transform(**loaded_datasets)
        self.save(output)

        end_time = time()
        elapsed = end_time - start_time
        self.save_metadata(elapsed)
        return output

    def transform(self, **app_args):
        raise NotImplementedError

    def set_attributes(self, sc, sc_sql, args):
        self.sc = sc
        self.sc_sql = sc_sql
        self.app_name = sc.appName
        self.job_name = self.get_job_name()  # differs from app_name when one spark app runs several jobs.
        self.args = args
        self.set_job_yml()
        self.set_paths()
        self.set_is_incremental()
        self.set_frequency()

    def get_job_name(self):
        # Isolated in function for overridability
        job_file = self.get_job_file()
        # when run from Flow(), job_file is full path. When run from ETL directly, job_file is "jobs/..." .
        # TODO change this hacky way to deal with it.
        return job_file.replace(GIT_REPO+'jobs/','').replace('jobs/','')# .replace('.py','')  # TODO make better with os.path functions.

    def get_job_file(self):
        return inspect.getsourcefile(self.__class__)

    def set_job_yml(self):
        meta_file = CLUSTER_APP_FOLDER+JOBS_METADATA_FILE if self.args['storage']=='s3' else JOBS_METADATA_LOCAL_FILE
        yml = self.load_meta(meta_file)
        try:
            self.job_yml = yml[self.job_name]
        except KeyError:
            raise KeyError("Your job '{}' can't be found in jobs_metadata file '{}'. Add it there or make sure the name matches".format(self.job_name, meta_file))

    def set_paths(self):
        self.INPUTS = self.job_yml['inputs']
        self.OUTPUT = self.job_yml['output']

    def set_is_incremental(self):
        self.is_incremental = any([self.INPUTS[item].get('inc_field', None) is not None for item in self.INPUTS.keys()])

    def set_frequency(self):
        self.frequency = self.job_yml.get('frequency', None)

    def load_inputs(self, loaded_inputs):
        app_args = {}
        for item in self.INPUTS.keys():

            # Load from memory if available
            if item in loaded_inputs.keys():
                app_args[item] = loaded_inputs[item]
                print "Input '{}' passed in memory from a previous job.".format(item)
                continue

            # Load from disk
            path = self.INPUTS[item]['path']
            path = Path_Handler(path).expand_later(self.args['storage'])
            app_args[item] = self.load_data(path, self.INPUTS[item]['type'])
            print "Input '{}' loaded from files '{}'.".format(item, path)

        if self.is_incremental:
            app_args = self.filter_incremental_inputs(app_args)

        self.sql_register(app_args)
        return app_args

    def filter_incremental_inputs(self, app_args):
        min_dt = self.get_output_max_timestamp()

        # Get latest timestamp in common across incremental inputs
        maxes = []
        for item in app_args.keys():
            input_is_tabular = self.INPUTS[item]['type'] in ('csv', 'parquet')  # TODO: register as part of function
            inc = self.INPUTS[item].get('inc_field', None)
            if input_is_tabular and inc:
                max_dt = app_args[item].agg({inc: "max"}).collect()[0][0]
                maxes.append(max_dt)
        max_dt = min(maxes) if len(maxes)>0 else None

        # Filter
        for item in app_args.keys():
            input_is_tabular = self.INPUTS[item]['type'] in ('csv', 'parquet')  # TODO: register as part of function
            inc = self.INPUTS[item].get('inc_field', None)
            if inc:
                if input_is_tabular:
                    inc_type = {k:v for k, v in app_args[item].dtypes}[inc]
                    print "Input dataset '{}' will be filtered for min_dt={} max_dt={}".format(item, min_dt, max_dt)
                    if min_dt:
                        # min_dt = to_date(lit(s)).cast(TimestampType()  # TODO: deal with dt type, as coming from parquet
                        app_args[item] = app_args[item].filter(app_args[item][inc] > min_dt)
                    if max_dt:
                        app_args[item] = app_args[item].filter(app_args[item][inc] <= max_dt)
                else:
                    raise "Incremental loading is not supported for unstructured input. You need to handle the incremental logic in the job code."
        return app_args

    def sql_register(self, app_args):
        for item in app_args.keys():
            input_is_tabular = hasattr(app_args[item], "rdd")  # assuming DataFrame will keep 'rdd' attribute
            if input_is_tabular:
                app_args[item].createOrReplaceTempView(item)

    def load_data(self, path, path_type):
        if path_type == 'txt':
            return self.sc.textFile(path)
        elif path_type == 'csv':
            return self.sc_sql.read.csv(path, header=True)
        elif path_type == 'parquet':
            return self.sc_sql.read.parquet(path)
        else:
            supported = ['txt', 'csv', 'parquet']  # TODO: register types differently without duplicating
            raise "Unsupported file type '{}' for path '{}'. Supported types are: {}. ".format(path_type, path, supported)

    def get_output_max_timestamp(self):
        path = self.OUTPUT['path']
        path += '*' # to go into subfolders
        try:
            df = self.load_data(path, self.OUTPUT['type'])
        except Exception as e:  # TODO: don't catch all
            print "Previous increment could not be loaded or doesn't exist. It will be ignored. Folder '{}' failed loading with error '{}'.".format(path, e)
            return None

        dt = df.agg({self.OUTPUT['inc_field']: "max"}).collect()[0][0]
        print "Max timestamp of previous increment: '{}'".format(dt)
        return dt

    def save(self, output):
        path = Path_Handler(self.OUTPUT['path']).expand_now()

        if self.is_incremental:
            current_time = datetime.utcnow().strftime('%Y%m%d_%H%M%S_utc')
            path += 'inc_%s/'%current_time

        # TODO: deal with cases where "output" is df when expecting rdd, or at least raise issue in a cleaner way.
        if self.OUTPUT['type'] == 'txt':
            output.saveAsTextFile(path)
        elif self.OUTPUT['type'] == 'parquet':
            output.write.parquet(path)
        elif self.OUTPUT['type'] == 'csv':
            output.write.option("header", "true").csv(path)

        print 'Wrote output to ',path
        self.path = path

    def save_metadata(self, elapsed):
        fname = self.path + '_metadata.txt'
        content = """
            -- app_name: %s
            -- job_name: %s
            -- time (s): %s
            -- cluster_setup : TBD
            -- input folders : TBD
            -- output folder : TBD
            -- github hash: TBD
            -- code: TBD
            """%(self.app_name, self.job_name, elapsed)
        FS_Ops_Dispatcher().save_metadata(fname, content, self.args['storage'])

    def query(self, query_str):
        print 'Query string:', query_str
        return self.sc_sql.sql(query_str)

    @staticmethod
    def load_meta(fname):
        with open(fname, 'r') as stream:
            yml = yaml.load(stream)
        return yml


class FS_Ops_Dispatcher():
    def save_metadata(self, fname, content, storage):
        self.save_metadata_cluster(fname, content) if storage=='s3' else self.save_metadata_local(fname, content)

    @staticmethod
    def save_metadata_local(fname, content):
        fh = open(fname, 'w')
        fh.write(content)
        fh.close()

    @staticmethod
    def save_metadata_cluster(fname, content):
        bucket_name = fname.split('s3://')[1].split('/')[0]  # TODO: remove redundancy
        bucket_fname = '/'.join(fname.split('s3://')[1].split('/')[1:])  # TODO: remove redundancy
        fake_handle = StringIO.StringIO(content)
        s3c = boto3.client('s3')
        s3c.put_object(Bucket=bucket_name, Key=bucket_fname, Body=fake_handle.read())

    def listdir(self, path, storage):
        return self.listdir_cluster(path) if storage=='s3' else self.listdir_local(path)

    @staticmethod
    def listdir_local(path):
        return os.listdir(path)

    @staticmethod
    def listdir_cluster(path):
        bucket_name = path.split('s3://')[1].split('/')[0]
        prefix = '/'.join(path.split('s3://')[1].split('/')[1:])
        client = boto3.client('s3')
        objects = client.list_objects(Bucket=bucket_name, Prefix=prefix, Delimiter='/')
        paths = [item['Prefix'].split('/')[-2] for item in objects.get('CommonPrefixes')]
        return paths

    def dir_exist(self, path, storage):
        return self.dir_exist_cluster(path) if storage=='s3' else self.dir_exist_local(path)

    @staticmethod
    def dir_exist_local(path):
        return os.path.isdir(path)

    @staticmethod
    def dir_exist_cluster(path):
        raise "Not implemented"


class Path_Handler():
    def __init__(self, path):
        self.path = path

    def expand_later(self, storage):
        path = self.path
        if '{latest}' in path:
            upstream_path = path.split('{latest}')[0]
            paths = FS_Ops_Dispatcher().listdir(upstream_path, storage)
            latest_date = max(paths)
            path = path.format(latest=latest_date)
        return path

    def expand_now(self):
        path = self.path
        if '{now}' in path:
            current_time = datetime.utcnow().strftime('%Y%m%d_%H%M%S_utc')
            path = path.format(now=current_time)
        return path

    def get_base(self):
        if '{latest}' in self.path:
            return self.path.split('{latest}')[0]
        elif '{now}' in self.path:
            return self.path.split('{now}')[0]
        else:
            return self.path


class CommandLiner():
    def __init__(self, job_class_or_name, **args):
        #TODO to add later to run class as standalone (not called by ETL_Base)
        self.args = args
        parser = self.define_commandline_args()
        cmd_args = parser.parse_args()
        self.args.update(cmd_args.__dict__)  # commandline arguments take precedence over function ones.

        job_file = job_class_or_name if isinstance(job_class_or_name, basestring) else get_job_file(job_class_or_name)
        # import ipdb; ipdb.set_trace()
        job_class = get_job_class(job_class_or_name) if isinstance(job_class_or_name, basestring) else job_class_or_name
        job_name = job_file.replace('jobs/','')
        if args['execution'] == 'run':
            # self.launch_run_mode(app_name, self.args, etl_func, flow_class)
            # self.launch_run_mode2(job_class, job_name, self.args)
            self.launch_run_mode2(job_class, self.args)
        elif args['execution'] == 'deploy':
            self.launch_deploy_mode(job_file, **self.args)

    # # def commandline_launch(self, app_name, job_file, args, etl_func, flow_class):
    # def commandline_launch(self, app_name, job_file, args, etl_func):
    #     """
    #     This function is used to run the job locally or deploy it to aws and run it there.
    #     The inputs should not be dependent on whether the job is run locally or deployed to cluster as it is used for both.
    #     """
    #     # TODO: avoid having to specify funct input params that may not be used (app_name, job_file, callback)
    #     self.args = args
    #     parser = self.define_commandline_args()
    #     cmd_args = parser.parse_args()
    #     self.args.update(cmd_args.__dict__)  # commandline arguments take precedence over function ones.
    #     if args['execution'] == 'run':
    #         self.launch_run_mode(app_name, self.args, etl_func)
    #     elif args['execution'] == 'deploy':
    #         self.launch_deploy_mode(job_file, **self.args)

    @staticmethod
    def define_commandline_args():
        # Defined here separatly for overridability.
        parser = argparse.ArgumentParser()
        parser.add_argument("-e", "--execution", default='run', help="Choose 'run' (default) or 'deploy'.", choices=set(['deploy', 'run'])) # comes from cmd line since value is set when running on cluster
        parser.add_argument("-l", "--storage", default='local', help="Choose 'local' (default) or 's3'.", choices=set(['local', 's3'])) # comes from cmd line since value is set when running on cluster
        parser.add_argument("-a", "--aws_setup", default='perso', help="Choose aws setup from conf/config.cfg, typically 'prod' or 'dev'. Only relevant if choosing to deploy to a cluster.")
        parser.add_argument("-d", "--dependencies", action='store_true', help="Run dependencies with this job")
        # For later : --job_metadata_file, --machines, to be integrated only as a way to overide values from file.
        return parser

    # def launch_run_mode(self, app_name, args, etl_func):
    #     # Load spark here instead of at module level to remove dependency on spark when only deploying code to aws.
    #     from pyspark import SparkContext
    #     from pyspark.sql import SQLContext
    #     sc = SparkContext(appName=app_name)
    #     sc_sql = SQLContext(sc)
    #     if not self.args['dependencies']:
    #         etl_func(sc, sc_sql, args)
    #     else:
    #         Flow(sc, sc_sql, args, app_name)

    def launch_run_mode2(self, job_class, args):
        # Load spark here instead of at module level to remove dependency on spark when only deploying code to aws.
        from pyspark import SparkContext
        from pyspark.sql import SQLContext
        app_name = get_job_file(job_class)
        sc = SparkContext(appName=app_name)
        sc_sql = SQLContext(sc)
        if not self.args['dependencies']:
            job_class.etl(sc, sc_sql, args)
        else:
            Flow(sc, sc_sql, args, app_name)

    def launch_deploy_mode(self, job_file, aws_setup, **app_args):
        # Load deploy lib here instead of at module level to remove dependency on it when running code locally
        from core.deploy import DeployPySparkScriptOnAws
        DeployPySparkScriptOnAws(app_file=job_file, aws_setup=aws_setup, **app_args).run()  # TODO: fix mismatch job vs app.


def get_job_file(job_class):
    return inspect.getsourcefile(job_class.__class__)

def get_job_class(name):
    name_import = name.replace('/','.').replace('.py','')
    import_cmd = "from jobs.{} import Job".format(name_import)
    exec(import_cmd)
    return Job


import networkx as nx
import random
import pandas as pd


class Flow():
    def __init__(self, sc, sc_sql, args, job):
        self.job = job
        storage = args['storage']
        df = self.create_connections_jobs(storage)
        DG = self.create_master_graph(df)  # from top to bottom
        tree = self.create_tree(DG, 'upstream', job, include_dup=False) # from bottom to top
        leafs = self.get_leafs_recursive(tree, leafs=[])
        print 'Sequence of jobs to be run', leafs

        # load all job classes and run them
        df = {}
        for leaf in leafs:
            job_class = self.get_class_from(leaf)  # TODO: support loading sql jobs.
            job_obj = job_class()
            job_obj.set_attributes(sc, sc_sql, args)  # TODO: check if call duplicated downstream
            loaded_inputs = {}
            for in_name, in_properties in job_obj.job_yml['inputs'].iteritems():
                if in_properties.get('from'):
                    loaded_inputs[in_name] = df[in_properties['from']]
            # print 'Already loaded inputs for jobs {}: {}'.format(leaf, loaded_inputs) # TODO: keep print at job loading level but could pass name of prev job that dataset came from.
            df[leaf] = job_obj.etl(sc, sc_sql, args, loaded_inputs)

    def get_leafs_recursive(self, tree, leafs):
        """Recursive function to extract all leafs in order out of tree.
        Each pass, jobs are moved from "tree" to "leafs" variables until done.
        """
        cur_leafs = [node for node in tree.nodes() if tree.in_degree(node)!=0 and tree.out_degree(node)==0]
        leafs += cur_leafs

        for leaf in cur_leafs:
            tree.remove_node(leaf)

        if len(tree.nodes()) >= 2:
            self.get_leafs_recursive(tree, leafs)

        return leafs + tree.nodes()


    @staticmethod
    def get_class_from(name):
        # TODO: use get_job_class instead.
        name_import = name.replace('/','.')
        import_cmd = "from jobs.{} import Job".format(name_import)
        exec(import_cmd)
        return Job

    def create_connections_path(self, storage):
        meta_file = CLUSTER_APP_FOLDER+JOBS_METADATA_FILE if storage=='s3' else JOBS_METADATA_LOCAL_FILE # TODO: don't repeat from etl_base
        yml = ETL_Base.load_meta(meta_file)

        connections = []
        for job_name, job_meta in yml.iteritems():
            output_path = Path_Handler(job_meta['output']['path']).get_base()
            for input_name, input_meta in job_meta['inputs'].iteritems():
                input_path = Path_Handler(input_meta['path']).get_base()
                row = {'input_path': input_path, 'input_name': input_name, 'job_name':job_name, 'output_path': output_path, 'output_name':job_name+'_output'}
                connections.append(row)

        return pd.DataFrame(connections)

    def create_connections_jobs(self, storage):
        meta_file = CLUSTER_APP_FOLDER+JOBS_METADATA_FILE if storage=='s3' else JOBS_METADATA_LOCAL_FILE # TODO: don't repeat from etl_base
        yml = ETL_Base.load_meta(meta_file)

        connections = []
        for job_name, job_meta in yml.iteritems():
            dependencies = job_meta.get('dependencies') or []
            for dependency in dependencies:
                row = {'source_job': dependency, 'destination_job': job_name}
                connections.append(row)

        return pd.DataFrame(connections)

    def create_master_graph(self, df):
        """ Directed Graph from source to target. df must contain 'source_dataset' and 'target_dataset'.
        All other fields are attributed to target."""
        DG = nx.DiGraph()
        for ii, item in df.iterrows():
            item = item.to_dict()
            source_dataset = item.pop('source_job')
            target_dataset = item.pop('destination_job')
            item.update({'name':target_dataset})

            DG.add_edge(source_dataset, target_dataset)
            DG.add_node(source_dataset, {'name':source_dataset})
            DG.add_node(target_dataset, item)
        return DG

    def create_tree(self, DG, direction, root, include_dup=True):
        tree = nx.DiGraph()
        tree = self.create_tree_recursive(DG, tree, direction, root, include_dup)
        return tree

    def create_tree_recursive(self, DG, tree, direction, ref_node, include_dup=True):
        """ Builds tree recursively. Uses graph data structure but enforces tree to simplify downstream."""
        if direction == 'upstream':
            nodes = DG.predecessors(ref_node)
        elif direction == 'downstream':
            nodes = DG.successors(ref_node)
        else:
            raise 'Invalid "direction" input.'

        tree.add_node(ref_node, DG.node[ref_node])

        for item in nodes:
            if not tree.has_node(item):
                tree.add_edge(ref_node, item)
                tree.add_node(item, DG.node[item])
                self.create_tree_recursive(DG, tree, direction, item, include_dup)
            elif include_dup:
                ii = random.randint(1,1000001)  # TODO: find better (deterministic) way
                suffix_name = '_dup'
                suffix_id = '%s_%s'%(suffix_name, ii)
                child_id = item + suffix_id
                child_attributes = DG.node[item]
                child_attributes['name'] = item + suffix_name
                tree.add_edge(ref_node, child_id)
                tree.add_node(child_id, child_attributes)
        return tree
