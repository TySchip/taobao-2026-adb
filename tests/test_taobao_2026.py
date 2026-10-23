"""Synthetic UI fixtures, not captured device evidence."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

SOURCE = Path(__file__).resolve().parents[1] / 'taobao_2026.py'
spec = importlib.util.spec_from_file_location('assistant_script', SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def node(text, bounds='[40,500][750,600]', cls='android.widget.TextView', **attrs):
    return {'text': text, 'bounds': bounds, 'class': cls, **attrs}


def screen(*nodes):
    root = ET.Element('hierarchy')
    for attrs in nodes:
        ET.SubElement(root, 'node', attrs)
    return ET.tostring(root, encoding='unicode')


def task_screen(title='浏览好货(0/1)浏览好货+600', button='去完成'):
    return screen(node('做任务赚能量', '[0,100][1000,200]'), node(title),
                  node(button, '[800,510][1000,590]', 'android.widget.Button'))


def search_screen(history=True):
    nodes = [node('搜索后浏览立得奖励', '[500,100][1000,200]'),
             node('', '[30,280][1050,420]', 'android.widget.EditText'),
             node('搜索', '[850,290][1030,410]', 'android.widget.Button'),
             node('搜索发现', '[40,870][1000,1000]')]
    if history:
        nodes += [node('历史搜索', '[40,450][1000,588]'),
                  node('', '[40,585][300,685]', 'android.view.View',
                       **{'content-desc': '收纳盒', 'clickable': 'true'}),
                  node('收纳盒', '[90,600][250,670]')]
    return screen(*nodes)


def search_results():
    return screen(node('收纳盒', '[30,280][1050,420]', 'android.widget.EditText'),
                  node('搜索', '[850,290][1030,410]', 'android.widget.Button'),
                  node('收纳盒 ¥19.9', '[30,500][500,1500]', 'android.widget.Button'))


class FakeDevice:
    def __init__(self, xml, package=m.TB_APP):
        self.xml = xml
        self.package = package
        self.info = {'screenOn': True, 'currentPackageName': package}
        self.actions = []

    def app_current(self):
        return {'package': self.package}

    def dump_hierarchy(self):
        return self.xml

    def window_size(self):
        return (1080, 2400)

    def click(self, *args):
        self.actions.append(('click', args))

    def long_click(self, *args):
        self.actions.append(('long_click', args))

    def swipe(self, *args):
        self.actions.append(('swipe', args))

    def press(self, *args):
        self.actions.append(('press', args))

    def app_start(self, *args, **kwargs):
        self.actions.append(('app_start', args, kwargs))
        self.info['currentPackageName'] = m.TB_APP

class TaskTests(unittest.TestCase):
    def test_search_task_is_not_labeled_plain_browsing(self):
        task = m.task_candidates(m.parse_nodes(task_screen('搜一搜你心仪的宝贝(4/10)浏览15秒+400')))[0]
        self.assertEqual(task['action'], '搜索后浏览')

    def test_search_without_browse_word_is_supported(self):
        task = m.task_candidates(m.parse_nodes(task_screen('搜一搜你心仪的宝贝(0/5)+400')))[0]
        self.assertEqual(task['action'], '搜索后浏览')

    def test_search_entry_is_not_confused_with_product_results(self):
        entry, results = map(m.parse_nodes, (search_screen(), search_results()))
        self.assertTrue(m.is_search_entry(entry))
        self.assertFalse(m.is_search_results(entry))
        self.assertFalse(m.is_search_entry(results))
        self.assertTrue(m.is_search_results(results))

    def test_history_click_waits_for_results_and_does_not_scroll_entry(self):
        d = FakeDevice(search_screen())
        entry = m.parse_nodes(d.xml)
        d.xml = search_results()
        m.search_and_wait(d, entry)
        self.assertEqual(d.actions, [('click', (170, 635))])

    def test_no_history_sets_text_then_submits_search_once(self):
        d = FakeDevice(search_screen(False))
        entry = m.parse_nodes(d.xml)
        fields = []
        class Input:
            def __call__(self, **selector):
                fields.append(selector)
                return self
            def set_text(self, text):
                fields.append(text)
        with patch.object(FakeDevice, '__call__', Input(), create=True):
            with patch.object(m, 'read_screen', return_value=m.parse_nodes(search_results())):
                m.search_and_wait(d, entry)
        self.assertEqual(fields, [{'className': 'android.widget.EditText'}, '收纳盒'])
        self.assertEqual(d.actions, [('click', (940, 350))])

    def test_search_not_submitted_stops_without_swiping_or_repeated_clicks(self):
        d = FakeDevice(search_screen())
        with patch.object(m.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '搜索结果'):
            m.search_and_wait(d, m.parse_nodes(d.xml))
        self.assertEqual([a[0] for a in d.actions], ['click'])

    def test_search_verification_stops_without_swiping(self):
        d = FakeDevice(search_screen())
        entry = m.parse_nodes(d.xml)
        d.xml = screen(node('请完成滑动验证'))
        with self.assertRaisesRegex(RuntimeError, '验证'):
            m.search_and_wait(d, entry)
        self.assertEqual([a[0] for a in d.actions], ['click'])

    def test_search_required_on_unknown_page_never_swipes(self):
        d = FakeDevice(screen(node('页面加载中')))
        with patch.object(m.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '搜索结果'):
            m.browse_and_return(d, 18, search_required=True)
        self.assertEqual(d.actions, [])

    def test_normal_task_landing_on_search_page_searches_before_swiping(self):
        d = FakeDevice(search_screen())
        events = []
        def search(*args):
            events.append('search')
        def read(_):
            return m.parse_nodes(search_screen() if not events else task_screen())
        d.swipe = lambda *args: events.append('swipe')
        with patch.object(m, 'Thread') as thread, patch.object(m, 'read_screen', side_effect=read), patch.object(m, 'search_and_wait', side_effect=search), patch.object(m.time, 'monotonic', side_effect=[0, 0, 1]):
            thread.return_value.is_alive.return_value = False
            m.browse_and_return(d, 1)
        self.assertEqual(events, ['search', 'swipe'])

    def test_search_task_routes_to_search_flow_and_checks_progress(self):
        d = FakeDevice(task_screen('搜一搜你心仪的宝贝(4/10)浏览15秒+400'))
        with patch.object(m, 'browse_and_return') as browse, patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        browse.assert_called_once_with(d, 18, search_required=True)

    def test_search_row_above_panel_header_is_not_clicked(self):
        xml = screen(node('做任务赚能量', '[0,500][1000,600]'),
                     node('搜一搜你心仪的宝贝(1/10)浏览15秒', '[40,220][750,330]'),
                     node('去完成', '[800,230][1000,320]', 'android.widget.Button'))
        self.assertEqual(m.task_candidates(m.parse_nodes(xml)), [])

    def test_results_page_does_not_submit_a_second_search(self):
        d = FakeDevice(search_results())
        with patch.object(m, 'Thread') as thread, patch.object(m, 'read_screen', side_effect=[m.parse_nodes(d.xml), m.parse_nodes(task_screen())]), patch.object(m.time, 'monotonic', side_effect=[0,0,1]), patch.object(m, 'search_and_wait') as search:
            thread.return_value.is_alive.return_value = False
            m.browse_and_return(d, 1, search_required=True)
        search.assert_not_called()
        self.assertEqual([a[0] for a in d.actions], ['swipe'])

    def test_default_preview_connects_to_selected_phone_without_clicks(self):
        d = FakeDevice(task_screen())
        adb = SimpleNamespace(adb=SimpleNamespace(device_list=lambda: [SimpleNamespace(serial='TEST_PHONE')]))
        u2 = SimpleNamespace(connect_usb=lambda serial: d if serial == 'TEST_PHONE' else None)
        with patch.dict(sys.modules, {'adbutils': adb, 'uiautomator2': u2}), patch.object(sys, 'argv', ['taobao_2026.py']), patch.object(m, 'configure_logging'), contextlib.redirect_stdout(io.StringIO()) as output:
            m.main()
        self.assertIn('预览未点击', output.getvalue())
        self.assertEqual(d.actions, [])

    def test_merged_2026_row_and_fullwidth_progress(self):
        tasks = m.task_candidates(m.parse_nodes(task_screen('逛一逛好货（0/2）浏览30秒+600')))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['progress'], (0, 2))

    def test_purchase_words_after_progress_are_not_lost(self):
        self.assertEqual(m.task_candidates(m.parse_nodes(task_screen('浏览好货(0/1)下单得能量'))), [])

    def test_unsupported_actions_are_skipped(self):
        for title in ('逛好货并分享(0/1)', '浏览开通会员(0/1)', '逛店铺并关注(0/1)', '去快手打卡领手机(0/1)点击去逛', '去微博看看热搜(0/1)跳转得奖励'):
            with self.subTest(title=title):
                self.assertEqual(m.task_candidates(m.parse_nodes(task_screen(title))), [])

    def test_alipay_related_visit_tasks_are_recognized(self):
        for title in ('逛一逛蚂蚁庄园(0/1)+800','去蚂蚁森林收能量(0/1)点击去逛','去芭芭农场领水果(0/1)浏览5秒+600','去支付宝(0/1)跳转得奖励',
                      '逛一逛支付宝金豆夺宝(0/1)点击去逛+400','去支付宝小游戏(0/1)浏览5秒',
                      '逛一逛支付宝新活动(0/1)浏览5秒','支付宝签到(0/1)浏览5秒'):
            with self.subTest(title=title):
                task=m.task_candidates(m.parse_nodes(task_screen(title)))[0]
                self.assertEqual(task['action'],'跳转停留6秒')

    def test_allowing_alipay_does_not_allow_payments_or_other_apps(self):
        for title in ('支付宝支付领红包(0/1)浏览5秒','逛蚂蚁庄园并订阅(0/1)','去支付宝和微博(0/1)跳转得奖励','拍立淘购物下单(0/1)浏览5秒'):
            with self.subTest(title=title):
                self.assertEqual(m.task_candidates(m.parse_nodes(task_screen(title))),[])

    def test_unknown_signin_and_non_alipay_games_are_not_candidates(self):
        for title in ('去签到领红包(0/1)浏览5秒+600', '去微博签到(0/1)浏览5秒',
                      '金豆夺宝(0/1)浏览5秒', '去玩小游戏(0/1)浏览5秒'):
            with self.subTest(title=title):
                self.assertEqual(m.task_candidates(m.parse_nodes(task_screen(title))), [])

    def test_task_title_cannot_pair_with_button_in_another_container(self):
        root=ET.fromstring(screen(node('做任务赚能量','[0,100][1000,200]')))
        first=ET.SubElement(root,'node',node('', '[0,400][1080,650]', 'android.view.View'))
        ET.SubElement(first,'node',node('浏览好货(0/1)'))
        second=ET.SubElement(root,'node',node('', '[0,400][1080,650]', 'android.view.View'))
        ET.SubElement(second,'node',node('去微博看看热搜(0/1)','[40,610][750,680]'))
        ET.SubElement(second,'node',node('去完成','[800,510][1000,590]','android.widget.Button'))
        self.assertEqual(m.task_candidates(m.parse_nodes(ET.tostring(root,encoding='unicode'))), [])

    def test_separate_row_subtitle_cannot_hide_weibo_destination(self):
        root=ET.fromstring(task_screen('浏览好货(0/1)'))
        ET.SubElement(root,'node',node('跳转微博得奖励','[40,570][750,600]'))
        self.assertEqual(m.task_candidates(m.parse_nodes(ET.tostring(root,encoding='unicode'))), [])

    def test_changed_button_position_is_rescanned_before_click(self):
        original=task_screen('浏览好货(0/1)')
        moved=screen(node('做任务赚能量','[0,100][1000,200]'),
                     node('去微博看看热搜(0/1)','[40,500][750,600]'),
                     node('去完成','[800,510][1000,590]','android.widget.Button'),
                     node('浏览好货(0/1)','[40,700][750,800]'),
                     node('去完成','[800,710][1000,790]','android.widget.Button'))
        d=FakeDevice(original)
        def dump():
            current=d.xml
            d.xml=moved
            return current
        d.dump_hierarchy=dump
        with patch.object(m,'browse_and_return'), patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        self.assertEqual([a[1] for a in d.actions if a[0]=='click'],[(900,750)])

    def test_replaced_task_is_not_clicked_at_old_position(self):
        d=FakeDevice(task_screen('浏览好货(0/1)'))
        def dump():
            current=d.xml
            d.xml=task_screen('去微博看看热搜(0/1)')
            return current
        d.dump_hierarchy=dump
        with patch.object(m,'browse_and_return') as browse, patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        browse.assert_not_called()
        self.assertFalse(any(a[0]=='click' for a in d.actions))

    def test_verification_before_task_click_never_clicks(self):
        d=FakeDevice(task_screen())
        def dump():
            current=d.xml
            d.xml=screen(node('请完成滑动验证'))
            return current
        d.dump_hierarchy=dump
        with patch.object(m,'browse_and_return'), self.assertRaisesRegex(RuntimeError,'验证'):
            m.run_tasks(d)
        self.assertEqual(d.actions,[])

    def test_in_app_weibo_or_kuaishou_landing_is_not_browsed(self):
        for title in ('微博热搜','快手'):
            with self.subTest(title=title):
                d=FakeDevice(screen(node(title)))
                with self.assertRaises(m.TaskSkipped):
                    m.browse_and_return(d,0)
                self.assertEqual(d.actions,[])

    def test_alipay_game_landing_only_waits_and_returns_without_playing(self):
        d=FakeDevice(screen(node('金豆夺宝'),node('开始游戏')),m.ALIPAY_APP)
        with patch.object(m,'return_to_tasks'):
            m.visit_and_return(d,0.03,allow_alipay=True)
        self.assertEqual(d.actions,[('app_start',(m.TB_APP,),{'stop':False})])

    def test_pailitao_uses_six_second_stay(self):
        task=m.task_candidates(m.parse_nodes(task_screen('拍立淘逛感兴趣的宝贝(2/5)浏览5秒+400')))[0]
        self.assertEqual(task['action'],'拍立淘停留6秒')

    def test_short_visit_routes_bypass_normal_browsing(self):
        for title,seconds,allow in [('逛一逛蚂蚁庄园(0/1)',6,True),('拍立淘逛感兴趣的宝贝(0/5)浏览5秒',6,False)]:
            d=FakeDevice(task_screen(title))
            def visit(*args,**kwargs):
                d.xml=screen(node('做任务赚能量'))
            with patch.object(m,'visit_and_return',side_effect=visit) as stay, patch.object(m,'browse_and_return') as browse, patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
                m.run_tasks(d)
            stay.assert_called_once_with(d,seconds,allow_alipay=allow)
            browse.assert_not_called()

    def test_alipay_package_allowed_only_for_explicit_visit(self):
        d=FakeDevice(screen(node('蚂蚁庄园')),m.ALIPAY_APP)
        with self.assertRaisesRegex(RuntimeError,'离开淘宝'):
            m.read_screen(d)
        self.assertTrue(m.read_screen(d,allowed_packages=(m.TB_APP,m.ALIPAY_APP)))

    def test_alipay_short_visit_waits_then_resumes_taobao_without_swipes(self):
        d=FakeDevice(screen(node('蚂蚁庄园')),m.ALIPAY_APP)
        started=m.time.monotonic()
        with patch.object(m,'return_to_tasks'):
            m.visit_and_return(d,0.03,allow_alipay=True)
        self.assertGreaterEqual(m.time.monotonic()-started,0.03)
        self.assertEqual(d.actions,[('app_start',(m.TB_APP,),{'stop':False})])

    def test_alipay_return_waits_for_asynchronous_taobao_focus(self):
        d=FakeDevice(screen(node('蚂蚁庄园')),m.ALIPAY_APP)
        def start(*args,**kwargs):
            d.actions.append(('app_start',args,kwargs))
        def resumed(_):
            d.info['currentPackageName']=m.TB_APP
            d.xml=task_screen()
        d.app_start=start
        with patch.object(m.time,'sleep',side_effect=resumed):
            m.visit_and_return(d,0.03,allow_alipay=True)
        self.assertEqual(d.actions,[('app_start',(m.TB_APP,),{'stop':False})])

    def test_pailitao_stay_only_presses_back(self):
        d=FakeDevice(screen(node('拍立淘结果')))
        with patch.object(m,'return_to_tasks'):
            m.visit_and_return(d,0.03)
        self.assertEqual(d.actions,[('press',('back',))])

    def test_verification_during_alipay_visit_stops_without_return_action(self):
        d=FakeDevice(screen(node('请完成滑动验证')),m.ALIPAY_APP)
        with patch.object(m,'return_to_tasks'),self.assertRaisesRegex(RuntimeError,'验证'):
            m.visit_and_return(d,0.1,allow_alipay=True)
        self.assertEqual(d.actions,[])

    def test_short_visit_does_not_allow_unrelated_app(self):
        d=FakeDevice(screen(node('内容')), 'com.example.other')
        with patch.object(m,'return_to_tasks'),self.assertRaises(RuntimeError):
            m.visit_and_return(d,0.03,allow_alipay=True)
        self.assertEqual(d.actions,[])

    def test_alipay_payment_page_stops_without_clicking(self):
        d=FakeDevice(screen(node('确认支付')),m.ALIPAY_APP)
        with patch.object(m,'return_to_tasks'),self.assertRaisesRegex(RuntimeError,'支付'):
            m.visit_and_return(d,0.1,allow_alipay=True)
        self.assertEqual(d.actions,[])

    def test_unpaired_button_is_skipped(self):
        xml = screen(node('去完成', '[800,510][1000,590]', 'android.widget.Button'))
        self.assertEqual(m.task_candidates(m.parse_nodes(xml)), [])

    def test_adjacent_row_is_not_paired(self):
        xml = screen(node('浏览好货(0/1)', '[40,300][750,400]'),
                     node('去完成', '[800,510][1000,590]', 'android.widget.Button'))
        self.assertEqual(m.task_candidates(m.parse_nodes(xml)), [])

    def test_ambiguous_overlapping_rows_are_skipped(self):
        xml = screen(node('浏览好货(0/1)'), node('浏览店铺(0/1)'),
                     node('去完成', '[800,510][1000,590]', 'android.widget.Button'))
        self.assertEqual(m.task_candidates(m.parse_nodes(xml)), [])

    def test_completed_browse_is_skipped_but_reward_is_recognized(self):
        self.assertEqual(m.task_candidates(m.parse_nodes(task_screen('浏览好货(1/1)'))), [])
        tasks = m.task_candidates(m.parse_nodes(task_screen('浏览好货(1/1)', '领取奖励')))
        self.assertEqual(tasks[0]['action'], '领取')

    def test_disabled_button_is_skipped(self):
        xml = task_screen().replace('text="去完成"', 'enabled="false" text="去完成"')
        self.assertEqual(m.task_candidates(m.parse_nodes(xml)), [])

    def test_verification_in_content_desc_without_bounds_stops(self):
        d = FakeDevice(screen(node('做任务赚能量'), {'content-desc': '请完成滑动验证'}))
        with self.assertRaisesRegex(RuntimeError, '验证'):
            m.read_screen(d)
        self.assertEqual(d.actions, [])

    def test_external_app_and_screen_off_stop_without_actions(self):
        for package, screen_on in [('com.example.other', True), (m.TB_APP, False)]:
            d = FakeDevice(task_screen(), package)
            d.info['screenOn'] = screen_on
            with self.assertRaises(RuntimeError):
                m.read_screen(d)
            self.assertEqual(d.actions, [])

    def test_stale_adb_app_current_does_not_override_active_ui_package(self):
        d = FakeDevice(task_screen(), 'com.example.stale_background')
        d.info['currentPackageName'] = m.TB_APP
        self.assertTrue(m.is_panel(m.read_screen(d)))
        self.assertEqual(d.actions, [])

    def test_active_external_ui_stops_even_if_adb_claims_taobao(self):
        d = FakeDevice(task_screen())
        d.info['currentPackageName'] = 'com.example.foreground'
        with self.assertRaisesRegex(RuntimeError, '离开淘宝'):
            m.read_screen(d)
        self.assertEqual(d.actions, [])

    def test_offscreen_button_is_never_tapped(self):
        d = FakeDevice(task_screen())
        with self.assertRaises(RuntimeError):
            m.center(d, (800,2350,1000,2450))
        self.assertEqual(d.actions, [])

    def test_unchanged_task_is_skipped_after_one_attempt(self):
        d = FakeDevice(task_screen())
        with patch.object(m, 'browse_and_return'), patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        self.assertEqual(sum(a[0] == 'click' for a in d.actions), 1)

    def test_duration_after_progress_is_honored(self):
        d = FakeDevice(task_screen('浏览好货(0/1)浏览60秒'))
        with patch.object(m, 'browse_and_return') as browse, patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        self.assertTrue(browse.called)
        self.assertEqual(browse.call_args.args[1], 63)

    def test_excessively_long_task_is_skipped_without_click(self):
        d = FakeDevice(task_screen('浏览好货(0/1)浏览300秒'))
        with patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        self.assertFalse(any(a[0]=='click' for a in d.actions))

    def test_order_page_stops_before_swipe(self):
        d = FakeDevice(screen(node('确认订单')))
        with patch.object(m.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '订单'):
            m.browse_and_return(d, 22)
        self.assertEqual(d.actions, [])

    def test_return_navigation_is_bounded(self):
        d = FakeDevice(screen(node('浏览好货')))
        with patch.object(m.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '返回'):
            m.browse_and_return(d, 0)
        self.assertEqual(sum(a[0] == 'press' for a in d.actions), 6)

    def test_browsing_swipes_without_sleep_or_full_hierarchy_reads(self):
        d = FakeDevice(screen(node('浏览15秒可领400')))
        events = []
        browser = m.parse_nodes(d.xml)
        panel = m.parse_nodes(task_screen())
        reads = iter((browser, panel))
        def read(_):
            events.append('read')
            return next(reads)
        d.swipe = lambda *args: events.append('swipe')
        with patch.object(m, 'Thread') as thread, patch.object(m, 'read_screen', side_effect=read), patch.object(m.time, 'monotonic', side_effect=[0, 0, 1, 2, 3]), patch.object(m.time, 'sleep') as sleep:
            thread.return_value.is_alive.return_value = False
            m.browse_and_return(d, 3)
        self.assertEqual(events, ['read', 'swipe', 'swipe', 'swipe', 'read'])
        sleep.assert_not_called()

    def test_failed_navigation_never_scrolls_task_panel(self):
        d = FakeDevice(task_screen())
        with patch.object(m.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '未进入浏览页'):
            m.browse_and_return(d, 22)
        self.assertEqual(d.actions, [])

    def test_verification_appearing_during_swipes_stops_before_next_gesture(self):
        d = FakeDevice(screen(node('浏览15秒可领400')))
        stop = Event()
        def swipe(*args):
            d.actions.append(('swipe', args))
            d.xml = screen(node('请完成滑动验证'))
            self.assertTrue(stop.wait(2), 'Background verification was not detected')
        d.swipe = swipe
        with patch.object(m, 'Event', return_value=stop), patch.object(m.time, 'monotonic', side_effect=[0,0,1,2,3]), patch.object(m.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '验证'):
            m.browse_and_return(d, 3)
        self.assertEqual([a[0] for a in d.actions], ['swipe'])

    def test_new_slider_prompt_is_detected_without_touch(self):
        d = FakeDevice(screen(node('请按住下方滑块拖动')))
        with self.assertRaisesRegex(RuntimeError, '验证'):
            m.read_screen(d)
        self.assertEqual(d.actions, [])

    def test_description_only_verification_interrupts_browsing(self):
        d = FakeDevice(screen(node('浏览15秒可领400')))
        stop = Event()
        def swipe(*args):
            d.actions.append(('swipe', args))
            d.xml = screen({'content-desc':'请按住下方滑块拖动'})
            self.assertTrue(stop.wait(2), 'Background description check did not stop browsing')
        d.swipe = swipe
        with patch.object(m, 'Event', return_value=stop), patch.object(m.time, 'monotonic', side_effect=[0,0,1,2,3]), self.assertRaisesRegex(RuntimeError, '验证'):
            m.browse_and_return(d, 3)
        self.assertEqual([a[0] for a in d.actions], ['swipe'])

    def test_monitor_query_failure_is_propagated_without_navigation(self):
        d = FakeDevice(screen(node('浏览15秒可领400')))
        browser = m.parse_nodes(d.xml)
        def thread(*, target, daemon):
            return SimpleNamespace(start=target, join=lambda **kwargs: None, is_alive=lambda: False)
        with patch.object(m, 'Thread', side_effect=thread), patch.object(m, 'read_screen', side_effect=[browser, RuntimeError('监测查询失败')]), self.assertRaisesRegex(RuntimeError, '监测查询失败'):
            m.browse_and_return(d, 3)
        self.assertEqual(d.actions, [])

    def test_monitor_timeout_stops_without_return_navigation(self):
        d = FakeDevice(screen(node('浏览15秒可领400')))
        with patch.object(m, 'Thread') as thread, patch.object(m.time, 'monotonic', side_effect=[0,0,1]), self.assertRaisesRegex(RuntimeError, '未及时返回'):
            thread.return_value.is_alive.return_value = True
            m.browse_and_return(d, 1)
        thread.return_value.join.assert_called_once_with(timeout=12)
        self.assertEqual([a[0] for a in d.actions], ['swipe'])

    def test_leaving_taobao_interrupts_before_next_swipe(self):
        d = FakeDevice(screen(node('浏览15秒可领400')))
        def swipe(*args):
            d.actions.append(('swipe', args))
            d.info['currentPackageName'] = 'com.example.other'
        d.swipe = swipe
        with patch.object(m.time, 'monotonic', side_effect=[0,0,1,2,3]), self.assertRaisesRegex(RuntimeError, '离开淘宝'):
            m.browse_and_return(d, 3)
        self.assertEqual([a[0] for a in d.actions], ['swipe'])

    def test_progress_increase_is_recognized_when_theme_title_changes(self):
        d = FakeDevice(task_screen('看看#卫衣(2/10)浏览15秒+400'))
        def browse(*args):
            d.xml = task_screen('新的主题(3/10)浏览15秒+400', '已完成')
        with patch.object(m, 'browse_and_return', side_effect=browse), patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        self.assertEqual(sum(a[0]=='click' for a in d.actions), 1)

    def test_missing_progress_finishes_normally_without_retry(self):
        d = FakeDevice(task_screen())
        def browse(*args):
            d.xml = screen(node('做任务赚能量'))
        with patch.object(m, 'browse_and_return', side_effect=browse), patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        self.assertEqual(sum(a[0]=='click' for a in d.actions), 1)

    def test_disappeared_one_off_task_rescans_and_runs_replacement(self):
        d = FakeDevice(task_screen('去省钱卡领红包(0/1)浏览5秒+600'))
        def browse(*args):
            clicks = sum(a[0]=='click' for a in d.actions)
            d.xml = task_screen('浏览第二项(0/1)浏览15秒') if clicks == 1 else screen(node('做任务赚能量'))
        with patch.object(m, 'browse_and_return', side_effect=browse), patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()) as out:
            m.run_tasks(d)
        self.assertEqual(sum(a[0]=='click' for a in d.actions), 2)
        self.assertIn('浏览第二项', out.getvalue())
        self.assertNotIn('已停止', out.getvalue())

    def test_no_progress_on_first_task_does_not_block_next_task(self):
        xml = task_screen('浏览第一项(0/1)浏览15秒')
        root = ET.fromstring(xml)
        ET.SubElement(root,'node',node('浏览第二项(0/1)浏览15秒', '[40,700][750,800]'))
        ET.SubElement(root,'node',node('去完成', '[800,710][1000,790]', 'android.widget.Button'))
        d = FakeDevice(ET.tostring(root,encoding='unicode'))
        with patch.object(m, 'browse_and_return'), patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        self.assertEqual([a[1] for a in d.actions if a[0]=='click'], [(900,550),(900,750)])

    def test_moved_task_uses_new_button_coordinates(self):
        d = FakeDevice(task_screen('浏览第一项(0/2)浏览15秒'))
        def browse(*args):
            if sum(a[0]=='click' for a in d.actions)==1:
                d.xml = screen(node('做任务赚能量'),
                               node('浏览第一项(1/2)浏览15秒','[40,700][750,800]'),
                               node('去完成','[800,710][1000,790]','android.widget.Button'))
            else:
                d.xml = screen(node('做任务赚能量'))
        with patch.object(m, 'browse_and_return', side_effect=browse), patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        self.assertEqual([a[1] for a in d.actions if a[0]=='click'], [(900,550),(900,750)])

    def test_task_failure_returns_to_list_and_runs_next(self):
        d = FakeDevice(task_screen('搜索好货(0/1)浏览15秒'))
        def browse(*args, **kwargs):
            if sum(a[0]=='click' for a in d.actions)==1:
                d.xml=search_screen()
                raise m.TaskSkipped('搜索结果未加载')
            d.xml=screen(node('做任务赚能量'))
        def press(*args):
            d.actions.append(('press',args))
            d.xml=task_screen('浏览第二项(0/1)浏览15秒')
        d.press=press
        with patch.object(m, 'browse_and_return', side_effect=browse), patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.run_tasks(d)
        self.assertEqual(sum(a[0]=='click' for a in d.actions),2)
        self.assertEqual(sum(a[0]=='press' for a in d.actions),1)

    def test_verification_between_tasks_stops_without_next_click(self):
        d=FakeDevice(task_screen())
        def browse(*args):
            d.xml=screen(node('请完成滑动验证'))
        with patch.object(m, 'browse_and_return',side_effect=browse), self.assertRaisesRegex(RuntimeError,'验证'):
            m.run_tasks(d)
        self.assertEqual([a[0] for a in d.actions],['click'])

    def test_return_reopens_task_list_from_activity_home(self):
        d=FakeDevice(screen(node('去赚能量',cls='android.widget.Button')))
        def click(*args):
            d.actions.append(('click',args))
            d.xml=task_screen()
        d.click=click
        with patch.object(m.time,'sleep'):
            self.assertTrue(m.is_panel(m.return_to_tasks(d)))
        self.assertEqual([a[0] for a in d.actions],['click'])

    def test_log_persists_event_without_ui_contents_or_secrets(self):
        before_handlers = set(m.LOG.handlers)
        with tempfile.TemporaryDirectory() as temp:
            try:
                with patch.object(m, 'LOG_DIR', Path(temp)), contextlib.redirect_stdout(io.StringIO()):
                    m.configure_logging()
                d = FakeDevice(screen(node('请按住下方滑块拖动'), node('SYNTHETIC_SECRET')))
                with self.assertRaises(RuntimeError):
                    m.read_screen(d)
                paths = list(Path(temp).glob('*.log'))
                self.assertEqual(len(paths), 1)
                data = paths[0].read_text(encoding='utf-8')
                self.assertIn('verification_detected', data)
                self.assertNotIn('SYNTHETIC_SECRET', data)
                self.assertNotIn('<hierarchy', data)
            finally:
                for handler in set(m.LOG.handlers) - before_handlers:
                    m.LOG.removeHandler(handler)
                    handler.close()

    def test_game_supports_new_and_old_text(self):
        for text in ('升级中 升级赛车拿钱 剩余 1200 能量', '跳一跳拿钱 剩余 30 体力'):
            _, energy = m.game_state(m.parse_nodes(screen(node(text, cls='android.widget.Button'))))
            self.assertIn(energy, (1200, 30))

    def test_game_low_energy_does_not_click(self):
        d = FakeDevice(screen(node('升级赛车拿钱 剩余 5 能量', cls='android.widget.Button')))
        with contextlib.redirect_stdout(io.StringIO()):
            m.upgrade(d)
        self.assertEqual(d.actions, [])

    def test_game_unchanged_energy_stops_after_three_attempts(self):
        d = FakeDevice(screen(node('升级赛车拿钱 剩余 100 能量', cls='android.widget.Button')))
        with patch.object(m.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, '三轮'):
            m.upgrade(d)
        self.assertEqual(sum(a[0] == 'long_click' for a in d.actions), 3)

    def test_game_without_energy_does_not_click(self):
        d = FakeDevice(screen(node('升级赛车拿钱', cls='android.widget.Button')))
        with self.assertRaises(RuntimeError):
            m.upgrade(d)
        self.assertEqual(d.actions, [])

    def test_upgrade_closes_reward_or_style_popup_then_resumes(self):
        for close in (node('关闭','[800,300][900,400]','android.widget.Button'),
                      node('图标','[800,300][900,400]','android.widget.ImageView', **{'content-desc':'关闭按钮'}),
                      node('','[800,300][900,400]','android.widget.ImageView', **{'resource-id':'com.taobao.taobao:id/iv_close'}),
                      node('×','[800,300][900,400]','android.view.View',clickable='true')):
            with self.subTest(close=close):
                d=FakeDevice(screen(node('确认选择'),node('送你500能量'),close,
                                    node('升级赛车拿钱 剩余 100 能量',cls='android.widget.Button')))
                def click(*args):
                    d.actions.append(('click',args))
                    d.xml=screen(node('升级赛车拿钱 剩余 100 能量',cls='android.widget.Button'))
                def upgrade(*args):
                    d.actions.append(('long_click',args))
                    d.xml=screen(node('升级赛车拿钱 剩余 5 能量',cls='android.widget.Button'))
                d.click=click
                d.long_click=upgrade
                with patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
                    m.upgrade(d)
                self.assertEqual(d.actions,[('click',(850,350)),('long_click',(395,550,3))])

    def test_upgrade_style_without_close_uses_back_not_confirm(self):
        d=FakeDevice(screen(node('选择车身款式'),node('确认选择',cls='android.widget.Button'),
                            node('升级赛车拿钱 剩余 100 能量',cls='android.widget.Button')))
        def back(*args):
            d.actions.append(('press',args))
            d.xml=screen(node('升级赛车拿钱 剩余 5 能量',cls='android.widget.Button'))
        d.press=back
        with patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.upgrade(d)
        self.assertEqual(d.actions,[('press',('back',))])

    def test_upgrade_failed_close_falls_back_to_back_once(self):
        d=FakeDevice(screen(node('解锁新尾翼'),node('关闭',cls='android.widget.Button')))
        def back(*args):
            d.actions.append(('press',args))
            d.xml=screen(node('升级赛车拿钱 剩余 5 能量',cls='android.widget.Button'))
        d.press=back
        with patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.upgrade(d)
        self.assertEqual([a[0] for a in d.actions],['click','press'])

    def test_upgrade_two_close_buttons_use_back_without_guessing(self):
        d=FakeDevice(screen(node('送你100能量'),node('关闭','[800,300][900,400]','android.widget.Button'),
                            node('关闭按钮','[800,700][900,800]','android.widget.Button')))
        def back(*args):
            d.actions.append(('press',args))
            d.xml=screen(node('升级赛车拿钱 剩余 5 能量',cls='android.widget.Button'))
        d.press=back
        with patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.upgrade(d)
        self.assertEqual(d.actions,[('press',('back',))])

    def test_upgrade_reopens_coin_entry_if_back_exits_activity(self):
        d=FakeDevice(screen(node('确认选择')))
        def back(*args):
            d.actions.append(('press',args))
            d.xml=screen(node('我的淘宝',clickable='true'))
        def click(*args):
            d.actions.append(('click',args))
            if len(d.actions)==2:
                d.xml=screen(node('淘金币抵¥108',clickable='true'))
            else:
                d.xml=screen(node('升级赛车拿钱 剩余 5 能量',cls='android.widget.Button'))
        d.press=back
        d.click=click
        with patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.upgrade(d)
        self.assertEqual([a[0] for a in d.actions],['press','click','click'])

    def test_upgrade_close_can_reopen_coin_entry_after_page_exit(self):
        d=FakeDevice(screen(node('关闭',cls='android.widget.Button')))
        def click(*args):
            d.actions.append(('click',args))
            d.xml=(screen(node('领淘金币',clickable='true')) if len(d.actions)==1 else
                   screen(node('升级赛车拿钱 剩余 5 能量',cls='android.widget.Button')))
        d.click=click
        with patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.upgrade(d)
        self.assertEqual([a[0] for a in d.actions],['click','click'])

    def test_upgrade_offscreen_close_uses_back(self):
        d=FakeDevice(screen(node('送你100能量'),node('关闭','[1000,2300][1200,2500]','android.widget.Button')))
        def back(*args):
            d.actions.append(('press',args))
            d.xml=screen(node('升级赛车拿钱 剩余 5 能量',cls='android.widget.Button'))
        d.press=back
        with patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.upgrade(d)
        self.assertEqual(d.actions,[('press',('back',))])

    def test_upgrade_popup_after_press_resets_stuck_energy_counter(self):
        d=FakeDevice(screen(node('升级赛车拿钱 剩余 100 能量',cls='android.widget.Button')))
        rounds=[]
        def upgrade(*args):
            d.actions.append(('long_click',args))
            rounds.append(1)
            if len(rounds)==3:
                d.xml=screen(node('送你100能量'),node('关闭',cls='android.widget.Button'),
                             node('升级赛车拿钱 剩余 200 能量',cls='android.widget.Button'))
            elif len(rounds)==4:
                d.xml=screen(node('升级赛车拿钱 剩余 5 能量',cls='android.widget.Button'))
        def close(*args):
            d.actions.append(('click',args))
            d.xml=screen(node('升级赛车拿钱 剩余 200 能量',cls='android.widget.Button'))
        d.long_click=upgrade
        d.click=close
        with patch.object(m.time,'sleep'), contextlib.redirect_stdout(io.StringIO()):
            m.upgrade(d)
        self.assertEqual([a[0] for a in d.actions],['long_click']*3+['click','long_click'])

    def test_upgrade_verification_or_payment_never_dismisses(self):
        for text in ('请完成滑动验证','确认支付'):
            d=FakeDevice(screen(node(text),node('关闭',cls='android.widget.Button')))
            with self.assertRaises(RuntimeError):m.upgrade(d)
            self.assertEqual(d.actions,[])

    def test_upgrade_stuck_popup_has_bounded_back_and_no_upgrade(self):
        d=FakeDevice(screen(node('确认选择')))
        with patch.object(m.time,'sleep'),self.assertRaises(RuntimeError):m.upgrade(d)
        self.assertEqual(d.actions,[('press',('back',))])

    def test_upgrade_does_not_close_sound_or_tailwing_progress(self):
        d=FakeDevice(screen(node('关闭音效',cls='android.widget.Button'),node('尾翼升级20%'),
                            node('升级赛车拿钱 剩余 5 能量',cls='android.widget.Button')))
        with contextlib.redirect_stdout(io.StringIO()):m.upgrade(d)
        self.assertEqual(d.actions,[])


if __name__ == '__main__':
    unittest.main(verbosity=2)
