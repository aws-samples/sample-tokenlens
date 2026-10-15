from tokenlens.tokens import count_message_tokens, count_tokens


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_count_tokens_positive():
    assert count_tokens("hello world") > 0


def test_count_tokens_scales_with_length():
    short = count_tokens("hello")
    long = count_tokens("hello " * 100)
    assert long > short


def test_count_message_tokens_string_content():
    messages = [{"role": "user", "content": "hello world"}]
    assert count_message_tokens(messages) == count_tokens("hello world")


def test_count_message_tokens_block_content():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image", "source": {}},
                {"type": "text", "text": "world"},
            ],
        }
    ]
    expected = count_tokens("hello") + count_tokens("world")
    assert count_message_tokens(messages) == expected
