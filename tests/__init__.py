"""The test tree is a package so every test module agrees on names.

Without this file pytest imports ``tests/workflow/conftest.py`` as
``workflow.conftest`` (it walks up while ``__init__.py`` exists and
stops at ``tests/``), while the test files import
``tests.workflow.conftest`` — TWO module objects, so every
module-level helper with state silently forks (verified: three
copies in ``sys.modules`` at once — ``conftest``,
``workflow.conftest``, ``tests.workflow.conftest``). The workflow
conftest's DB call tracker is shared state the drain reads; the
duplicate copies meant a drain called from a test body saw an empty
tracker and cancelled writers mid-database-call. Keep this file: the
package name pytest derives and the package name the tests import
must be the same one.
"""
