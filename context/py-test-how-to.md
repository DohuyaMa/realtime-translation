# How to capture warnings

Starting from version `<span class="pre">3.1</span>`, pytest now automatically catches warnings during test execution and displays them at the end of the session:

```
# content of test_show_warnings.py
importwarnings


defapi_v1():
    warnings.warn(UserWarning("api v1, should use functions from v2"))
    return 1


deftest_one():
    assert api_v1() == 1
```

Running pytest now produces this output: 

```
$ pytest test_show_warnings.py
=========================== test session starts ============================
platform linux -- Python 3.x.y, pytest-9.x.y, pluggy-1.x.y
rootdir: /home/sweet/project
collected 1 item

test_show_warnings.py .                                              [100%]

============================= warnings summary =============================
test_show_warnings.py::test_one
  /home/sweet/project/test_show_warnings.py:5: UserWarning: api v1, should use functions from v2
    warnings.warn(UserWarning("api v1, should use functions from v2"))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================= 1 passed, 1 warning in 0.12s =======================
```

## Controlling warnings

Similar to Python’s [warning filter](https://docs.python.org/3/library/warnings.html#warning-filter) and [`<span class="pre">-W</span><span> </span><span class="pre">option</span>`](https://docs.python.org/3/using/cmdline.html#cmdoption-W "(in Python v3.14)") flag, pytest provides its own `<span class="pre">-W</span>` flag to control which warnings are ignored, displayed, or turned into errors. See the [warning filter](https://docs.python.org/3/library/warnings.html#warning-filter) documentation for more advanced use-cases.

This code sample shows how to treat any `<span class="pre">UserWarning</span>` category class of warning as an error:

```
$ pytest -q test_show_warnings.py -W error::UserWarning
F                                                                    [100%]
================================= FAILURES =================================
_________________________________ test_one _________________________________

    def test_one():
>       assert api_v1() == 1
               ^^^^^^^^

test_show_warnings.py:10:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    def api_v1():
>       warnings.warn(UserWarning("api v1, should use functions from v2"))
E       UserWarning: api v1, should use functions from v2

test_show_warnings.py:5: UserWarning
========================= short test summary info ==========================
FAILED test_show_warnings.py::test_one - UserWarning: api v1, should use ...
1 failed in 0.12s
```

The same option can be set in the configuration file using the [`<span class="pre">filterwarnings</span>`](https://docs.pytest.org/en/stable/reference/reference.html#confval-filterwarnings) configuration option. For example, the configuration below will ignore all user warnings and specific deprecation warnings matching a regex, but will transform all other warnings into errors.

[X] toml

```
[pytest]
filterwarnings=[
"error",
"ignore::UserWarning",
# note the use of single quote below to denote "raw" strings in TOML
'ignore:function ham\(\) is deprecated:DeprecationWarning',
]
```

[ ] ini

When a warning matches more than one option in the list, the action for the last matching option is performed.

Note

The `<span class="pre">-W</span>` flag and the [`<span class="pre">filterwarnings</span>`](https://docs.pytest.org/en/stable/reference/reference.html#confval-filterwarnings) configuration option use warning filters that are similar in structure, but each configuration option interprets its filter differently. For example, *message* in `<span class="pre">filterwarnings</span>` is a string containing a regular expression that the start of the warning message must match, case-insensitively, while *message* in `<span class="pre">-W</span>` is a literal string that the start of the warning message must contain (case-insensitively), ignoring any whitespace at the start or end of message. Consult the [warning filter](https://docs.python.org/3/library/warnings.html#warning-filter) documentation for more details.

## `<span class="pre">@pytest.mark.filterwarnings</span>`

You can use the [@pytest.mark.filterwarnings](https://docs.pytest.org/en/stable/reference/reference.html#pytest-mark-filterwarnings-ref) mark to add warning filters to specific test items, allowing you to have finer control of which warnings should be captured at test, class or even module level:

```
importwarnings


defapi_v1():
    warnings.warn(UserWarning("api v1, should use functions from v2"))
    return 1


@pytest.mark.filterwarnings("ignore:api v1")
deftest_one():
    assert api_v1() == 1
```

You can specify multiple filters with separate decorators:

```
# Ignore "api v1" warnings, but fail on all other warnings
@pytest.mark.filterwarnings("ignore:api v1")
@pytest.mark.filterwarnings("error")
deftest_one():
    assert api_v1() == 1
```

Important

Regarding decorator order and filter precedence: it’s important to remember that decorators are evaluated in reverse order, so you have to list the warning filters in the reverse order compared to traditional [`<span class="pre">warnings.filterwarnings()</span>`](https://docs.python.org/3/library/warnings.html#warnings.filterwarnings "(in Python v3.14)") and [`<span class="pre">-W</span><span> </span><span class="pre">option</span>`](https://docs.python.org/3/using/cmdline.html#cmdoption-W "(in Python v3.14)") usage. This means in practice that filters from earlier [@pytest.mark.filterwarnings](https://docs.pytest.org/en/stable/reference/reference.html#pytest-mark-filterwarnings-ref) decorators take precedence over filters from later decorators, as illustrated in the example above.

Filters applied using a mark take precedence over filters passed on the command line or configured by the [`<span class="pre">filterwarnings</span>`](https://docs.pytest.org/en/stable/reference/reference.html#confval-filterwarnings) configuration option.

You may apply a filter to all tests of a class by using the [filterwarnings](https://docs.pytest.org/en/stable/reference/reference.html#pytest-mark-filterwarnings-ref) mark as a class decorator or to all tests in a module by setting the [`<span class="pre">pytestmark</span>`](https://docs.pytest.org/en/stable/reference/reference.html#globalvar-pytestmark) variable:

```
# turns all warnings into errors for this module
pytestmark = pytest.mark.filterwarnings("error")
```

Note

If you want to apply multiple filters (by assigning a list of [filterwarnings](https://docs.pytest.org/en/stable/reference/reference.html#pytest-mark-filterwarnings-ref) mark to [`<span class="pre">pytestmark</span>`](https://docs.pytest.org/en/stable/reference/reference.html#globalvar-pytestmark)), you must use the traditional [`<span class="pre">warnings.filterwarnings()</span>`](https://docs.python.org/3/library/warnings.html#warnings.filterwarnings "(in Python v3.14)") ordering approach (later filters take precedence), which is the reverse of the decorator approach mentioned above.

*Credits go to Florian Schulze for the reference implementation in the* [pytest-warnings](https://github.com/fschulze/pytest-warnings) *plugin.*

## Disabling warnings summary

Although not recommended, you can use the [`<span class="pre">--disable-warnings</span>`](https://docs.pytest.org/en/stable/reference/reference.html#cmdoption-disable-pytest-warnings) command-line option to suppress the warning summary entirely from the test run output.

## Disabling warning capture entirely

This plugin is enabled by default but can be disabled entirely in your configuration file with:

[X] toml

```
[pytest]
addopts=["-p","no:warnings"]
```

[ ] ini

Or passing `<span class="pre">-p</span><span> </span><span class="pre">no:warnings</span>` in the command-line. This might be useful if your test suites handles warnings using an external system.

## DeprecationWarning and PendingDeprecationWarning

By default pytest will display `<span class="pre">DeprecationWarning</span>` and `<span class="pre">PendingDeprecationWarning</span>` warnings from user code and third-party libraries, as recommended by [**PEP 565**](https://peps.python.org/pep-0565/). This helps users keep their code modern and avoid breakages when deprecated warnings are effectively removed.

However, in the specific case where users capture any type of warnings in their test, either with [`<span class="pre">pytest.warns()</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.warns "pytest.warns"), [`<span class="pre">pytest.deprecated_call()</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.deprecated_call "pytest.deprecated_call") or using the [`<span class="pre">recwarn</span>`](https://docs.pytest.org/en/stable/reference/reference.html#std-fixture-recwarn) fixture, no warning will be displayed at all.

Sometimes it is useful to hide some specific deprecation warnings that happen in code that you have no control over (such as third-party libraries), in which case you might use the warning filters options (configuration or marks) to ignore those warnings.

For example:

[X] toml

```
[pytest]
filterwarnings=[
'ignore:.*U.*mode is deprecated:DeprecationWarning',
]
```

[ ] ini

This will ignore all warnings of type `<span class="pre">DeprecationWarning</span>` where the start of the message matches the regular expression `<span class="pre">".*U.*mode</span><span> </span><span class="pre">is</span><span> </span><span class="pre">deprecated"</span>`.

See [@pytest.mark.filterwarnings](https://docs.pytest.org/en/stable/how-to/capture-warnings.html#filterwarnings) and [Controlling warnings](https://docs.pytest.org/en/stable/how-to/capture-warnings.html#controlling-warnings) for more examples.

Note

If warnings are configured at the interpreter level, using the [`<span class="pre">PYTHONWARNINGS</span>`](https://docs.python.org/3/using/cmdline.html#envvar-PYTHONWARNINGS "(in Python v3.14)") environment variable or the `<span class="pre">-W</span>` command-line option, pytest will not configure any filters by default.

Also pytest doesn’t follow [**PEP 565**](https://peps.python.org/pep-0565/) suggestion of resetting all warning filters because it might break test suites that configure warning filters themselves by calling [`<span class="pre">warnings.simplefilter()</span>`](https://docs.python.org/3/library/warnings.html#warnings.simplefilter "(in Python v3.14)") (see [#2430](https://github.com/pytest-dev/pytest/issues/2430) for an example of that).

## Ensuring code triggers a deprecation warning

You can also use [`<span class="pre">pytest.deprecated_call()</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.deprecated_call "pytest.deprecated_call") for checking that a certain function call triggers a `<span class="pre">DeprecationWarning</span>` or `<span class="pre">PendingDeprecationWarning</span>`:

```
importpytest


deftest_myfunction_deprecated():
    with pytest.deprecated_call():
        myfunction(17)
```

This test will fail if `<span class="pre">myfunction</span>` does not issue a deprecation warning when called with a `<span class="pre">17</span>` argument.

## Asserting warnings with the warns function

You can check that code raises a particular warning using [`<span class="pre">pytest.warns()</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.warns "pytest.warns"), which works in a similar manner to [raises](https://docs.pytest.org/en/stable/how-to/assert.html#assertraises) (except that [raises](https://docs.pytest.org/en/stable/how-to/assert.html#assertraises) does not capture all exceptions, only the `<span class="pre">expected_exception</span>`):

```
importwarnings

importpytest


deftest_warning():
    with pytest.warns(UserWarning):
        warnings.warn("my warning", UserWarning)
```

The test will fail if the warning in question is not raised. Use the keyword argument `<span class="pre">match</span>` to assert that the warning matches a text or regex. To match a literal string that may contain regular expression metacharacters like `<span class="pre">(</span>` or `<span class="pre">.</span>`, the pattern can first be escaped with `<span class="pre">re.escape</span>`.

Some examples:

```
with warns(UserWarning, match="must be 0 or None"):
    warnings.warn("value must be 0 or None", UserWarning)


with warns(UserWarning, match=r"must be \d+$"):
    warnings.warn("value must be 42", UserWarning)


with warns(UserWarning, match=r"must be \d+$"):
    warnings.warn("this is not here", UserWarning)

Traceback (most recent call last):
...
Failed: DID NOT WARN. No warnings of type ...UserWarning... were emitted...

with warns(UserWarning, match=re.escape("issue with foo() func")):
    warnings.warn("issue with foo() func")

```

You can also call [`<span class="pre">pytest.warns()</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.warns "pytest.warns") on a function or code string:

```
pytest.warns(expected_warning, func, *args, **kwargs)
pytest.warns(expected_warning, "func(*args, **kwargs)")
```

The function also returns a list of all raised warnings (as `<span class="pre">warnings.WarningMessage</span>` objects), which you can query for additional information:

```
with pytest.warns(RuntimeWarning) as record:
    warnings.warn("another warning", RuntimeWarning)

# check that only one warning was raised
assert len(record) == 1
# check that the message matches
assert record[0].message.args[0] == "another warning"
```

Alternatively, you can examine raised warnings in detail using the [`<span class="pre">recwarn</span>`](https://docs.pytest.org/en/stable/reference/reference.html#std-fixture-recwarn) fixture (see [below](https://docs.pytest.org/en/stable/how-to/capture-warnings.html#recwarn)).

The [`<span class="pre">recwarn</span>`](https://docs.pytest.org/en/stable/reference/reference.html#std-fixture-recwarn) fixture automatically ensures to reset the warnings filter at the end of the test, so no global state is leaked.

## Recording warnings

You can record raised warnings either using the [`<span class="pre">pytest.warns()</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.warns "pytest.warns") context manager or with the [`<span class="pre">recwarn</span>`](https://docs.pytest.org/en/stable/reference/reference.html#std-fixture-recwarn) fixture.

To record with [`<span class="pre">pytest.warns()</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.warns "pytest.warns") without asserting anything about the warnings, pass no arguments as the expected warning type and it will default to a generic Warning:

```
with pytest.warns() as record:
    warnings.warn("user", UserWarning)
    warnings.warn("runtime", RuntimeWarning)

assert len(record) == 2
assert str(record[0].message) == "user"
assert str(record[1].message) == "runtime"
```

The [`<span class="pre">recwarn</span>`](https://docs.pytest.org/en/stable/reference/reference.html#std-fixture-recwarn) fixture will record warnings for the whole function:

```
importwarnings


deftest_hello(recwarn):
    warnings.warn("hello", UserWarning)
    assert len(recwarn) == 1
    w = recwarn.pop(UserWarning)
    assert issubclass(w.category, UserWarning)
    assert str(w.message) == "hello"
    assert w.filename
    assert w.lineno
```

Both the [`<span class="pre">recwarn</span>`](https://docs.pytest.org/en/stable/reference/reference.html#std-fixture-recwarn) fixture and the [`<span class="pre">pytest.warns()</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.warns "pytest.warns") context manager return the same interface for recorded warnings: a [`<span class="pre">WarningsRecorder</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.WarningsRecorder "_pytest.recwarn.WarningsRecorder") instance. To view the recorded warnings, you can iterate over this instance, call `<span class="pre">len</span>` on it to get the number of recorded warnings, or index into it to get a particular recorded warning.

## Additional use cases of warnings in tests

Here are some use cases involving warnings that often come up in tests, and suggestions on how to deal with them:

* To ensure that **at least one** of the indicated warnings is issued, use:

```
deftest_warning():
    with pytest.warns((RuntimeWarning, UserWarning)):
        ...
```

* To ensure that **only** certain warnings are issued, use:

```
deftest_warning(recwarn):
    ...
    assert len(recwarn) == 1
    user_warning = recwarn.pop(UserWarning)
    assert issubclass(user_warning.category, UserWarning)
```

* To ensure that **no** warnings are emitted, use:

```
deftest_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ...
```

* To suppress warnings, use:

```
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    ...
```

## Custom failure messages

Recording warnings provides an opportunity to produce custom test failure messages for when no warnings are issued or other conditions are met.

```
deftest():
    with pytest.warns(Warning) as record:
        f()
        if not record:
            pytest.fail("Expected a warning!")
```

If no warnings are issued when calling `<span class="pre">f</span>`, then `<span class="pre">not</span><span> </span><span class="pre">record</span>` will evaluate to `<span class="pre">True</span>`. You can then call [`<span class="pre">pytest.fail()</span>`](https://docs.pytest.org/en/stable/reference/reference.html#pytest.fail "pytest.fail") with a custom error message.

## Internal pytest warnings

pytest may generate its own warnings in some situations, such as improper usage or deprecated features.

For example, pytest will emit a warning if it encounters a class that matches [`<span class="pre">python_classes</span>`](https://docs.pytest.org/en/stable/reference/reference.html#confval-python_classes) but also defines an `<span class="pre">__init__</span>` constructor, as this prevents the class from being instantiated:

```
# content of test_pytest_warnings.py
classTest:
    def__init__(self):
        pass

    deftest_foo(self):
        assert 1 == 1
```

```
$ pytest test_pytest_warnings.py -q

============================= warnings summary =============================
test_pytest_warnings.py:1
  /home/sweet/project/test_pytest_warnings.py:1: PytestCollectionWarning: cannot collect test class 'Test' because it has a __init__ constructor (from: test_pytest_warnings.py)
    class Test:

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1 warning in 0.12s
```

These warnings might be filtered using the same builtin mechanisms used to filter other types of warnings.

Please read our [Backwards Compatibility Policy](https://docs.pytest.org/en/stable/backwards-compatibility.html#backwards-compatibility) to learn how we proceed about deprecating and eventually removing features.

The full list of warnings is listed in [the reference documentation](https://docs.pytest.org/en/stable/reference/reference.html#warnings-ref).

## Resource Warnings

Additional information of the source of a [`<span class="pre">ResourceWarning</span>`](https://docs.python.org/3/library/exceptions.html#ResourceWarning "(in Python v3.14)") can be obtained when captured by pytest if [`<span class="pre">tracemalloc</span>`](https://docs.python.org/3/library/tracemalloc.html#module-tracemalloc "(in Python v3.14)") module is enabled.

One convenient way to enable [`<span class="pre">tracemalloc</span>`](https://docs.python.org/3/library/tracemalloc.html#module-tracemalloc "(in Python v3.14)") when running tests is to set the [`<span class="pre">PYTHONTRACEMALLOC</span>`](https://docs.python.org/3/using/cmdline.html#envvar-PYTHONTRACEMALLOC "(in Python v3.14)") to a large enough number of frames (say `<span class="pre">20</span>`, but that number is application dependent).

For more information, consult the [Python Development Mode](https://docs.python.org/3/library/devmode.html) section in the Python documentation.
