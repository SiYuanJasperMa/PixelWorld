from copy import deepcopy
import json
from pixelworld.context_replay import compact


def test_compaction_preserves_task_images_and_recent_tools(tmp_path):
    task={'type':'message','role':'user','content':[{'type':'input_image','image_url':'data:image/png;base64,AA=='},{'type':'input_text','text':'Physical prompt'}]}
    items=[task]
    for i in range(4):
        items += [{'type':'function_call','name':'exec_command','call_id':str(i),'arguments':'x'*1000},
                  {'type':'function_call_output','call_id':str(i),'output':'Process exited with code 0\n'+'y'*1000}]
    original=deepcopy(items)
    result,report=compact(items,keep_recent=2,archive_dir=tmp_path)
    assert items==original and result[0]==task
    assert result[-4:]==items[-4:]
    assert report['tool_pairs_compacted']==2 and report['saved_bytes']>0
    for record in report['archived']:
        file=tmp_path/record['relative_path'].split('/')[-1]
        assert json.loads(file.read_text())==record['original']


def test_image_calls_and_unfinished_calls_are_never_removed():
    items=[{'type':'function_call','name':'view_image','call_id':'image','arguments':'first_frame.png'},
           {'type':'function_call_output','call_id':'image','output':[{'type':'input_image','image_url':'data:image/png;base64,AA=='}]},
           {'type':'function_call','name':'exec_command','call_id':'a','arguments':'short'},
           {'type':'function_call_output','call_id':'a','output':'done'},
           {'type':'function_call','name':'exec_command','call_id':'unfinished','arguments':'still running'}]
    result,report=compact(items,keep_recent=1)
    assert result==items and report['tool_pairs_compacted']==0


def test_generated_image_history_is_archived_but_input_image_is_kept(tmp_path):
    import base64
    source={'type':'message','role':'user','content':[{'type':'input_image','image_url':'data:image/png;base64,AA=='}]}
    items=[source]
    for i,name in enumerate(['first_frame.png','inspection.png','inspection.png','inspection.png']):
        items += [{'type':'function_call','name':'view_image','call_id':str(i),'arguments':json.dumps({'path':name})},
                  {'type':'function_call_output','call_id':str(i),'output':[{'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(b'png').decode()}]}]
    result,report=compact(items,keep_recent=1,keep_generated_images=1,archive_dir=tmp_path)
    assert result[0]==source
    assert items[1] in result and items[2] in result
    assert result[-2:]==items[-2:]
    assert report['tool_pairs_compacted']==2
    assert len(list(tmp_path.glob('*.png')))==2


def test_latest_inspection_is_kept_even_with_many_later_text_tools():
    picture=[{'type':'function_call','name':'view_image','call_id':'v','arguments':json.dumps({'path':'inspection.png'})},
             {'type':'function_call_output','call_id':'v','output':[{'type':'input_image','image_url':'data:image/png;base64,AA=='}]}]
    items=list(picture)
    for i in range(6):
        items += [{'type':'function_call','name':'exec_command','call_id':str(i),'arguments':'{}'},
                  {'type':'function_call_output','call_id':str(i),'output':'done'}]
    result,_=compact(items,keep_recent=2,keep_generated_images=1)
    assert picture[0] in result and picture[1] in result


def test_task_and_rules_file_results_remain_in_replay():
    protected=[{'type':'function_call','name':'exec_command','call_id':'task','arguments':json.dumps({'cmd':'cat prompt.txt GENERATION_RULES.md task_spec.json'})},
               {'type':'function_call_output','call_id':'task','output':'Actual benchmark prompt and all rules'}]
    items=list(protected)
    for i in range(6):
        items += [{'type':'function_call','name':'exec_command','call_id':str(i),'arguments':'{}'},
                  {'type':'function_call_output','call_id':str(i),'output':'ordinary result'}]
    result,_=compact(items,keep_recent=2)
    assert protected[0] in result and protected[1] in result
