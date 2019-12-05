# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Uses bisection to determine which commit a bug was introduced and fixed.
This module takes a high and a low commit SHA, a repo name, and a bug.
The module bisects the high and low commit SHA searching for the location
where the bug was introduced. It also looks for where the bug was solved.
This is done with the following steps:


  NOTE: NEEDS TO BE RUN FROM THE oss-fuzz HOME directory

  Typical usage example:
    1. (Host) Clone the main project repo on the host
    2. (Host) Run git fetch --unshallow
    3. (Host) Use git bisect to identify the next commit to check
    4. (Host) Build the image at the specific commit
    5. (Host) Mount the repo with the correct commit over the build image repo
    5. (Host) Build the fuzzers from new image with updated repo
    6. (Host) Test for bug’s existence
    7. Go to step 3
    python bisect.py --project_name curl
      --commit_new dda418266c99ceab368d723facb52069cbb9c8d5
      s
      --fuzzer_name curl_fuzzer_ftp
      --test_case /usr/local/google/home/lneat/Downloads/clusterfuzz-test\
          case-minimized-curl_fuzzer_ftp-5657400807260160
"""

import argparse

from DockerRepoManager import DockerRepoManager
from helper import build_fuzzers
from helper import reproduce


def main():
  parser = argparse.ArgumentParser(
      'bisector.py',
      description='git bisection for finding introduction of bugs')

  parser.add_argument(
      '--project_name',
      help='The name of the project where the bug occured',
      required=True)
  parser.add_argument(
      '--commit_new',
      help='The newest commit SHA to be bisected',
      required=True)
  parser.add_argument(
      '--commit_old',
      help='The oldest commit SHA to be bisected',
      required=True)
  parser.add_argument(
      '--fuzzer_name', help='the name of the fuzzer to be built', required=True)
  parser.add_argument(
      '--test_case', help='the test_case to be reproduced', required=True)
  parser.add_argument('--engine', default='libfuzzer')
  parser.add_argument(
      '--sanitizer',
      default='address',
      help='the default is "address"; "dataflow" for "dataflow" engine')
  parser.add_argument('--architecture', default='x86_64')

  args = parser.parse_args()

  rm = DockerRepoManager(args.project_name)
  commit_list = rm.get_commit_list(args.commit_old, args.commit_new)
  result_commit_idx = bisection(0,
                                len(commit_list) - 1, commit_list, rm,
                                len(commit_list), args)
  if result_commit_idx == -1:
    print('No error was found in commit range %s:%s' % (args.commit_old, args.commit_new))
  elif result_commit_idx == len(commit_list):
    print('Error was found through full commit range %s:%s' % (args.commit_old, args.commit_new))
  else:
    print('Error was introduced at commit %s' % commit_list[result_commit_idx])


def bisection_display(commit_list, last_error, current_index):
  """Displays the current state of the binary search.

  Args:
    commit_list: The total list of commits
    last_error: The index of the last error that occured
    current_index: The current index being checked
  """
  print()
  print('Current Bisection Status')
  print('newest commit')
  for i in range(0, len(commit_list)):
    if i == current_index:
      print('%s %s' % (commit_list[i], 'current_index'))
    elif i == last_error:
      print('%s %s' % (commit_list[i], 'Most recent error found'))
    else:
      print('%s' % (commit_list[i]))
  print('oldest commit')


def bisection(commit_new_idx, commit_old_idx, commit_list, repo_manager,
              last_error, args):
  """Returns the commit ID where a bug was introduced.

  Args:
    commit_old_idx: The oldest commit SHA index in the search space
    commit_new_idx: The newest commit SHA index in the search space
    commit_list: The list of all commit SHAs
    repo_manager: The class handling all of the git repo calls
    last_error: The index where the last error was found
    args: Struct containing info about how the fuzzers should be built

  Returns:
    The index of the SHA string where the bug was introduced
  """
  cur_idx = (commit_new_idx + commit_old_idx) // 2
  error_exists = test_error_exists(commit_list[cur_idx], repo_manager, args)

  bisection_display(commit_list, last_error, cur_idx)
  if commit_new_idx == commit_old_idx:
    if error_exists:
      return cur_idx
    else:
      return last_error

  if error_exists:
    return bisection(cur_idx +1, commit_old_idx, commit_list, repo_manager,
                       cur_idx, args)
  else:
    if cur_idx == 0:
      return -1
    return bisection(commit_new_idx, cur_idx - 1, commit_list, repo_manager,
                     last_error, args)


def build_fuzzers_from_helper(project_name, args):
  """Builds fuzzers using helper.py api.
  Args:
    project_name: the name of the project whos fuzzers you want build
  """
  parser = argparse.ArgumentParser()
  parser.add_argument('project_name')
  parser.add_argument('fuzzer_name', nargs='?')
  parser.add_argument('--engine', default='libfuzzer')
  parser.add_argument(
      '--sanitizer',
      default='address',
      help='the default is "address"; "dataflow" for "dataflow" engine')
  parser.add_argument('--architecture', default='x86_64')
  parser.add_argument(
      '-e', action='append', help='set environment variable e.g. VAR=value')
  parser.add_argument('source_path', help='path of local source', nargs='?')
  parser.add_argument(
      '--clean',
      dest='clean',
      action='store_true',
      help='clean existing artifacts.')
  parser.add_argument(
      '--no-clean',
      dest='clean',
      action='store_false',
      help='do not clean existing artifacts '
      '(default).')
  parser.set_defaults(clean=False)
  args = parser.parse_args([project_name, args.fuzzer_name,
                            '--clean',
                            '--engine', args.engine,
                            '--sanitizer', args.sanitizer,
                            '--architecture', args.architecture])
  build_fuzzers(args, True)


def reproduce_error(project_name, test_case, fuzzer_name):
  """Checks to see if the error is repoduceable at a specific commit.
  Args:
    project_name: The name of the project you are testing
    test_case: The path to the test_case you are passing in
    fuzzer_name: The name of the fuzz target to be tested
  Returns:
    True if the error still exists
  """
  parser = argparse.ArgumentParser()
  parser.add_argument('project_name', help='name of the project')
  parser.add_argument('fuzzer_name', help='name of the fuzzer')
  parser.add_argument('testcase_path', help='path of local testcase')
  parser.add_argument(
      'fuzzer_args',
      help='arguments to pass to the fuzzer',
      nargs=argparse.REMAINDER)
  parser.add_argument(
      '--valgrind', action='store_true', help='run with valgrind')
  parser.add_argument(
      '-e', action='append', help='set environment variable e.g. VAR=value')
  args = parser.parse_args([project_name, fuzzer_name, test_case])
  return reproduce(args)


def test_error_exists(commit, repo_manager, args):
  """Tests if the error is reproduceable at the specified commit

  Args:
    commit: The commit you want to check for the error
    repo_manager: The object that handles git interaction
    args: Struct containing info about how fuzzers should be built

  Returns:
    True if the error exists at the specified commit
  """
  repo_manager.set_image_commit(commit)
  build_fuzzers_from_helper(repo_manager.repo_name, args)
  err_code = reproduce_error(repo_manager.repo_name, args.test_case, args.fuzzer_name)
  if err_code == 0:
    print('Error does not exist at commit %s' % commit)
    return False
  else:
    print('Error exists at commit %s' % commit)
    return True


if __name__ == '__main__':
  main()
