from tools.publication_coverage import coverage


def test_coverage_requires_current_hash_and_trace_receipt():
    cat={'updated_utc':'now','groups':[{'suite':'s','model_key':'m','track':'text','path':'s/m/text/index.json'}]}
    idx={'s/m/text/index.json':{'cases':{'0001':[
        {'status':'complete','detail':'a','detail_sha256':'same'},
        {'status':'failed','detail':'b','detail_sha256':'changed'},
        {'status':'running','detail':'c'},
    ]}}}
    ledger={'ready':{'a':{'source_sha256':'same','trace_ready':True,'trace_format_version':3},
                     'b':{'source_sha256':'old','trace_ready':True,'trace_format_version':3}}}
    report=coverage(cat,idx,ledger)
    assert report['totals']=={'running':1,'closed_records':2,'details_uploaded':1,'details_pending':1,'traces_uploaded':1,'traces_pending':1}
