'use strict';
'require view';
'require form';
'require fs';
'require tools.widgets as widgets';

return view.extend({
    load: function() {
        return Promise.all([
            L.resolveDefault(fs.list('/sys/class/thermal'), []),
            L.resolveDefault(fs.list('/sys/class/hwmon'), []).then(function(entries) {
                return Promise.all(entries.filter(function(e) {
                    return /^hwmon[0-9]+$/.test(e.name);
                }).map(function(e) {
                    var dir = '/sys/class/hwmon/' + e.name;
                    return L.resolveDefault(fs.list(dir), []).then(function(files) {
                        return files.filter(function(f) { return /^temp[0-9]+_input$/.test(f.name); })
                            .map(function(f) { return dir + '/' + f.name; });
                    });
                })).then(function(paths) { return [].concat.apply([], paths); });
            })
        ]);
    },

    render: function(data) {
        var m = new form.Map('esp32monitor', _('ESP32 Monitor'),
            _('Provide router status to ESP32 over HTTP. Endpoint: /cgi-bin/esp32-status. Sampling takes approximately one second; traffic rates are bytes per second.'));
        var s = m.section(form.NamedSection, 'main', 'esp32monitor');
        var o;

        o = s.option(form.Flag, 'enabled', _('Enable status endpoint'));
        o.default = '0';
        o.rmempty = false;

        o = s.option(form.Value, 'api_token', _('API token'),
            _('Use 8–128 letters, digits, underscores or hyphens. A random token is generated on installation.'));
        o.password = true;
        o.rmempty = false;
        o.validate = function(section_id, value) {
            return /^[A-Za-z0-9_-]{8,128}$/.test(value || '') || _('Enter 8–128 URL-safe characters.');
        };

        o = s.option(widgets.DeviceSelect, 'wan_device', _('WAN device'),
            _('Leave empty for automatic detection. This identifies WAN and does not filter monitored interfaces.'));
        o.noaliases = true;
        o.nocreate = true;
        o.rmempty = true;

        o = s.option(widgets.DeviceSelect, 'interfaces', _('Monitored interfaces'),
            _('Leave empty to monitor all interfaces except loopback.'));
        o.multiple = true;
        o.noaliases = true;
        o.nocreate = true;
        o.rmempty = true;

        o = s.option(form.Flag, 'only_link_up', _('Only connected interfaces'));
        o.default = '1';
        o.rmempty = false;

        o = s.option(form.Value, 'temperature_source', _('Temperature sensor'),
            _('Automatically use the first available sensor, or select a sysfs temperature path. Unavailable sensors return null.'));
        o.value('auto', _('Automatic'));
        data[0].filter(function(e) { return /^thermal_zone[0-9]+$/.test(e.name); }).forEach(function(e) {
            var path = '/sys/class/thermal/' + e.name + '/temp';
            o.value(path, path);
        });
        data[1].forEach(function(path) { o.value(path, path); });
        o.default = 'auto';
        o.rmempty = false;
        o.validate = function(section_id, value) {
            return value === 'auto' || /^\/sys\/class\/(thermal\/thermal_zone[0-9]+\/temp|hwmon\/hwmon[0-9]+\/temp[0-9]+_input)$/.test(value || '') || _('Select a valid temperature sensor path.');
        };

        o = s.option(form.Flag, 'allow_query_token', _('Allow token in URL'),
            _('Allow ?token=TOKEN for clients without custom header support. Otherwise use the X-API-Token header. URLs may appear in access logs.'));
        o.default = '1';
        o.rmempty = false;
        return m.render();
    }
});
