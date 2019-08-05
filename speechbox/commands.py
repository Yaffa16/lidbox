"""
Command definitions for all tools.
"""
import argparse
import collections
import itertools
import json
import os
import pprint
import sys

import speechbox
import speechbox.dataset as dataset
import speechbox.preprocess.features as features
import speechbox.preprocess.transformations as transformations
import speechbox.system as system
import speechbox.models as models


def create_argparser():
    parser = argparse.ArgumentParser(prog=speechbox.__name__, description=speechbox.__doc__)
    subparsers = parser.add_subparsers(
        title="tools",
        description="subcommands for different tasks",
    )
    # Create command line options for all valid commands
    for cmd in all_commands:
        subparser = cmd.create_argparser(subparsers)
        subparser.set_defaults(cmd_class=cmd)
    return parser


class ExpandAbspath(argparse.Action):
    """Simple argparse action to expand path arguments to full paths using os.path.abspath."""
    def __call__(self, parser, namespace, path, *args, **kwargs):
        setattr(namespace, self.dest, os.path.abspath(path))


class Command:
    """Base command with common helpers for all subcommands."""

    tasks = tuple()

    @classmethod
    def create_argparser(cls, subparsers):
        parser = subparsers.add_parser(cls.__name__.lower(), description=cls.__doc__)
        parser.add_argument("cache_dir",
            type=str,
            action=ExpandAbspath,
            help="Speechbox cache for storing intermediate output such as extracted features.")
        parser.add_argument("experiment_config",
            type=str,
            action=ExpandAbspath,
            help="Path to a yaml-file containing the experiment configuration, e.g. hyperparameters, feature extractors etc.")
        parser.add_argument("--verbosity", "-v",
            action="count",
            default=0,
            help="Increases verbosity of output for each -v supplied (up to 3).")
        parser.add_argument("--run-cProfile",
            action="store_true",
            help="Do profiling on all commands and write results into a file in the working directory.")
        parser.add_argument("--src",
            type=str,
            action=ExpandAbspath,
            help="Source directory, depends on context.")
        parser.add_argument("--dst",
            type=str,
            action=ExpandAbspath,
            help="Target directory, depends on context.")
        parser.add_argument("--load-state",
            action="store_true",
            help="Load command state from the cache directory.")
        parser.add_argument("--save-state",
            action="store_true",
            help="Save command state to the cache directory.")
        return parser

    def __init__(self, args):
        self.args = args
        self.dataset_id = None
        self.experiment_config = {}
        self.state = {}

    def args_src_ok(self):
        args = self.args
        ok = True
        if not args.src:
            print("Error: Specify dataset source directory with --src.", file=sys.stderr)
            ok = False
        elif not os.path.isdir(args.src):
            print("Error: Source directory '{}' does not exist.".format(args.src), file=sys.stderr)
            ok = False
        return ok

    def args_dst_ok(self):
        args = self.args
        ok = True
        if not args.dst:
            print("Error: Specify dataset destination directory with --dst.", file=sys.stderr)
            ok = False
        elif not os.path.isdir(args.dst):
            if args.verbosity:
                print("Creating destination directory '{}'".format(args.dst))
            os.makedirs(args.dst)
        return ok

    def state_data_ok(self):
        ok = True
        if "data" not in self.state:
            error_msg = (
                "Error: self.state does not have a 'data' key containing filepaths and labels, cannot extract features."
                " Either load an existing dataset definition from the cache with '--load-state' or create a new dataset split."
                "\nSee e.g. 'speechbox dataset --help'."
            )
            print(error_msg, file=sys.stderr)
            ok = False
        return ok

    def load_state(self):
        args = self.args
        state_json = os.path.join(args.cache_dir, "state.json")
        if args.verbosity:
            print("Loading state from '{}'".format(state_json))
        with open(state_json) as f:
            self.state = json.load(f)

    def save_state(self):
        args = self.args
        if not os.path.isdir(args.cache_dir):
            if args.verbosity:
                print("Creating cache directory '{}'".format(args.cache_dir))
            os.makedirs(args.cache_dir)
        state_json = os.path.join(args.cache_dir, "state.json")
        if args.verbosity:
            print("Saving state to '{}'".format(state_json))
        with open(state_json, "w") as f:
            json.dump(self.state, f)

    def run(self):
        args = self.args
        if args.verbosity > 1:
            print("Running tool '{}' with arguments:".format(self.__class__.__name__.lower()))
            pprint.pprint(vars(args))
            print()
        if args.verbosity:
            print("Loading experiment config from '{}'".format(args.experiment_config))
        self.experiment_config = system.load_yaml(args.experiment_config)
        if args.verbosity > 1:
            print("Experiment config is:")
            pprint.pprint(self.experiment_config)
            print()
        self.dataset_id = self.experiment_config["dataset_id"]
        if args.load_state:
            self.load_state()
        if args.verbosity > 1:
            print("Running with initial state:")
            pprint.pprint(self.state, depth=3)
            print()

    def run_tasks(self):
        given_tasks = [getattr(self, task_name) for task_name in self.__class__.tasks if getattr(self.args, task_name)]
        if not given_tasks:
            print("Error: No tasks given, doing nothing", file=sys.stderr)
            return 2
        for task in given_tasks:
            ret = task()
            if ret:
                return ret

    def exit(self):
        if self.args.save_state:
            self.save_state()


class Dataset(Command):
    """Dataset analysis and manipulation."""

    tasks = ("walk", "parse", "split", "check")

    @classmethod
    def create_argparser(cls, subparsers):
        parser = super().create_argparser(subparsers)
        parser.add_argument("--walk",
            action="store_true",
            help="Walk over a dataset, printing wavpath-label pairs.")
        parser.add_argument("--parse",
            action="store_true",
            help="Parse a dataset according to parameters set in the config file, given as '--config-file'.")
        parser.add_argument("--resampling-rate",
            type=int,
            help="If given with --parse, all wavfile output will be resampled to this sampling rate.")
        parser.add_argument("--check",
            action="store_true",
            help="Walk over a dataset checking every file. Might take a long time since every file will be opened.")
        parser.add_argument("--split",
            choices=dataset.all_split_types,
            help="Create a random training-validation-test split for a dataset. Use --save-state to store paths into the cache_dir in state.json.")
        return parser

    def walk(self):
        args = self.args
        if args.verbosity:
            print("Walking over dataset '{}'".format(self.dataset_id))
        if not self.args_src_ok():
            return 1
        walker_config = {
            "dataset_root": args.src,
            "sampling_rate_override": args.resampling_rate,
        }
        dataset_walker = dataset.get_dataset_walker(self.dataset_id, walker_config)
        for label, wavpath in dataset_walker.walk(verbosity=args.verbosity):
            print(wavpath, label)

    def check(self):
        args = self.args
        if args.verbosity:
            print("Checking integrity of dataset '{}'".format(self.dataset_id))
        if "data" in self.state:
            if args.verbosity:
                print("Dataset files defined in self.state, checking all files by group")
                print("Checking that the dataset data groups are disjoint by file contents")
            datagroups = self.state["data"]
            for a, b in itertools.combinations(datagroups.keys(), r=2):
                print("'{}' vs '{}' ... ".format(a, b), flush=True, end='')
                # Group all filepaths by hashes on the file contents
                duplicates = collections.defaultdict(list)
                for path in itertools.chain(datagroups[a]["paths"], datagroups[b]["paths"]):
                    duplicates[system.md5sum(path)].append(path)
                # Filter out all singleton groups
                duplicates = [paths for paths in duplicates.values() if len(paths) > 1]
                if duplicates:
                    print("error: datasets not disjoint, following files have equal content hashes:")
                    for paths in duplicates:
                        for path in paths:
                            print(path)
                else:
                    print("ok")
            if args.verbosity:
                print("Checking all audio files in the dataset")
            dataset_walker = dataset.get_dataset_walker(self.dataset_id)
            for datagroup_name, datagroup in self.state["data"].items():
                paths, labels = datagroup["paths"], datagroup["labels"]
                if args.verbosity:
                    print("'{}', containing {} paths and {} labels, of which {} labels are unique".format(datagroup_name, len(paths), len(labels), len(set(labels))))
                dataset_walker.overwrite_target_paths(paths, labels)
                for _ in dataset_walker.walk(check_duplicates=True, check_read=True, verbosity=args.verbosity):
                    pass
        else:
            if args.verbosity:
                print("Dataset datagroups not defined in self.state, checking dataset from its root directory '{}'".format(args.src))
            if not self.args_src_ok():
                return 1
            walker_config = {
                "dataset_root": args.src,
            }
            dataset_walker = dataset.get_dataset_walker(self.dataset_id, walker_config)
            for _ in dataset_walker.walk(check_duplicates=True, check_read=True, verbosity=args.verbosity):
                pass

    def parse(self):
        args = self.args
        if args.verbosity:
            print("Parsing dataset '{}'".format(self.dataset_id))
        if not (self.args_src_ok() and self.args_dst_ok()):
            return 1
        parser_config = {
            "dataset_root": args.src,
            "output_dir": args.dst,
            "resampling_rate": args.resampling_rate,
        }
        parser = dataset.get_dataset_parser(self.dataset_id, parser_config)
        num_parsed = 0
        if not args.verbosity:
            for _ in parser.parse():
                num_parsed += 1
        else:
            for output in parser.parse():
                num_parsed += 1
                if any(output):
                    status, out, err = output
                    msg = "Warning:"
                    if status:
                        msg += " exit code: {}".format(status)
                    if out:
                        msg += " stdout: '{}'".format(out)
                    if err:
                        msg += " stderr: '{}'".format(err)
                    print(msg)
        if args.verbosity:
            print(num_parsed, "files processed")

    def split(self):
        args = self.args
        if args.verbosity:
            print("Creating a training-validation-test split for dataset '{}' using split type '{}'".format(self.dataset_id, args.split))
        if not self.args_src_ok():
            return 1
        walker_config = {
            "dataset_root": args.src,
        }
        dataset_walker = dataset.get_dataset_walker(self.dataset_id, walker_config)
        self.state["label_to_index"] = dataset_walker.make_label_to_index_dict()
        if args.split == "by-speaker":
            splitter = transformations.dataset_split_samples_by_speaker
        else:
            splitter = transformations.dataset_split_samples
        training_set, validation_set, test_set = splitter(dataset_walker, verbosity=args.verbosity)
        self.state["data"] = {
            "training": {
                "paths": training_set[0],
                "labels": training_set[1]
            },
            "validation": {
                "paths": validation_set[0],
                "labels": validation_set[1]
            },
            "test": {
                "paths": test_set[0],
                "labels": test_set[1]
            }
        }

    def run(self):
        super().run()
        return self.run_tasks()


class Preprocess(Command):
    """Feature extraction."""

    tasks = ("extract_features",)

    @classmethod
    def create_argparser(cls, subparsers):
        parser = super().create_argparser(subparsers)
        parser.add_argument("--extract-features",
            action="store_true",
            help="Perform feature extraction on whole dataset.")
        return parser

    def extract_features(self):
        args = self.args
        if args.verbosity:
            print("Starting feature extraction")
        if not self.state_data_ok():
            return 1
        config = self.experiment_config
        label_to_index = self.state["label_to_index"]
        for datagroup_name, datagroup in self.state["data"].items():
            if args.verbosity:
                print("Datagroup '{}' has {} audio files".format(datagroup_name, len(datagroup["paths"])))
            labels, paths = datagroup["labels"], datagroup["paths"]
            utterances = transformations.speech_dataset_to_utterances(
                labels, paths,
                utterance_length_ms=config["utterance_length_ms"],
                utterance_offset_ms=config["utterance_offset_ms"],
                apply_vad=config.get("apply_vad", False)
            )
            features = transformations.utterances_to_features(
                utterances,
                label_to_index=label_to_index,
                extractors=config["extractors"],
                sequence_length=config["sequence_length"]
            )
            target_path = os.path.join(args.cache_dir, datagroup_name)
            wrote_path = system.write_features(features, target_path)
            datagroup["features"] = wrote_path
            if args.verbosity:
                print("Wrote '{}' features to '{}'".format(datagroup_name, wrote_path))

    def run(self):
        super().run()
        return self.run_tasks()


class Train(Command):
    """Model training."""

    @classmethod
    def create_argparser(cls, subparsers):
        parser = super().create_argparser(subparsers)
        parser.add_argument("--load-model",
            action="store_true",
            help="Load pre-trained model from cache directory.")
        parser.add_argument("--save-model",
            action="store_true",
            help="Save model to the cache directory, overwriting any existing models with the same name.")
        parser.add_argument("--model-id",
            type=str,
            help="Use this value as the model name instead of the one in the experiment yaml-file.")
        return parser

    def train(self):
        args = self.args
        if args.verbosity:
            print("Preparing model for training")
        if not self.state_data_ok():
            return 1
        data = self.state["data"]
        model_config = self.experiment_config["model"]
        if args.verbosity > 1:
            print("\nModel config is:")
            pprint.pprint(model_config)
            print()
        model = self.state["model"]
        # Load training set consisting of pre-extracted features
        training_set, training_set_meta = system.load_features_as_dataset(
            # List of all .tfrecord files containing all training set samples
            [data["training"]["features"]],
            model_config
        )
        # Same for the validation set
        validation_set, _ = system.load_features_as_dataset(
            [data["validation"]["features"]],
            model_config
        )
        model.prepare(training_set_meta, model_config)
        if args.verbosity:
            print("\nStarting training\n")
        model.fit(training_set, validation_set, model_config)
        if args.verbosity:
            print("\nTraining finished\n")

    def run(self):
        super().run()
        args = self.args
        self.model_id = args.model_id if args.model_id else self.experiment_config["model"]["name"]
        if args.load_model:
            if args.verbosity:
                print("Loading model '{}' from the cache directory".format(self.model_id))
            self.state["model"] = models.KerasWrapper.from_disk(args.cache_dir, self.model_id)
        else:
            if args.verbosity:
                print("Creating new model '{}'".format(self.model_id))
            self.state["model"] = models.KerasWrapper(self.model_id)
        return self.train()

    def exit(self):
        args = self.args
        if args.save_model:
            if "model" not in self.state:
                print("Error: no model to save")
                return 1
            saved_path = self.state["model"].to_disk(args.cache_dir)
            if args.verbosity:
                print("Wrote model as '{}'".format(saved_path))
        super().exit()


class Evaluate(Command):
    """Prediction and evaluation using trained models."""

    tasks = ("evaluate_test_set",)

    @classmethod
    def create_argparser(cls, subparsers):
        parser = super().create_argparser(subparsers)
        parser.add_argument("--model-id",
            type=str,
            help="Use this value as the model name instead of the one in the experiment yaml-file.")
        parser.add_argument("--evaluate-test-set",
            action="store_true",
            help="Evaluate model on test set")
        return parser

    def evaluate_test_set(self):
        args = self.args
        if args.verbosity:
            print("Preparing model for evaluation")
        if not self.state_data_ok():
            return 1
        self.model_id = args.model_id if args.model_id else self.experiment_config["model"]["name"]
        if args.verbosity:
            print("Loading model '{}' from the cache directory".format(self.model_id))
        model = models.KerasWrapper.from_disk(args.cache_dir, self.model_id)
        model_config = self.experiment_config["model"]
        test_set, _ = system.load_features_as_dataset(
            [self.state["data"]["test"]["features"]],
            model_config
        )
        model.evaluate(test_set, model_config)

    def run(self):
        super().run()
        return self.run_tasks()


all_commands = (
    Dataset,
    Preprocess,
    Train,
    Evaluate,
)
