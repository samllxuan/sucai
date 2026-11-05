from tracker.worker.meta_browser import matches_exact_phrase, search_url


def record(title='', body='', page='DramaWave'):
    return {'ad_archive_id':'123', 'page_id':'456', 'page_name':page,
            'snapshot':{'title':title, 'body':{'text':body}, 'images':[]}}


def test_full_phrase_must_appear_in_visible_text():
    query = 'I Got Three Girlfriends From the System'
    assert matches_exact_phrase(record(title=query.upper()), query)
    assert matches_exact_phrase(record(body='Watch: I got three girlfriends from the system!'), query)
    assert not matches_exact_phrase(record(title='Three girlfriends and a system'), query)
    assert not matches_exact_phrase(record(body='A completely different drama'), query)


def test_exact_phrase_does_not_match_urls_and_respects_word_boundaries():
    r = record(title='Unrelated')
    r['snapshot']['link_url'] = 'https://example.com/I-Got-Three-Girlfriends-From-the-System'
    assert not matches_exact_phrase(r, 'I Got Three Girlfriends From the System')
    assert not matches_exact_phrase(record(title='DramaBoxed'), 'DramaBox')
    assert 'keyword_exact_phrase' in search_url('a b', 'US', 'keyword', 'ACTIVE', 'exact')
    assert 'keyword_unordered' in search_url('a b', 'US', 'keyword', 'ACTIVE', 'broad')
