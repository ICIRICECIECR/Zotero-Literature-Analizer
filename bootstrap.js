/*
 * Trae Literature Interpretation Plugin for Zotero 7+
 * bootstrap.js - Plugin lifecycle, menu integration, and main logic
 */

var TraeLitInterp = {
  _rootURI: null,
  _scriptDir: null,
  _initialized: false,

  _pluginID: "trae-lit-interp@example.com",
  _version: "1.0.13",
  _menuRegistered: false,

  init: function ({ id, version, rootURI }) {
    if (this._initialized) return;
    this._initialized = true;
    this._rootURI = rootURI;
    this._version = version || "1.0.0";

    this._setDefaults();

    var dataDir = Zotero.DataDirectory.dir;
    this._scriptDir = PathUtils.join(dataDir, "trae-lit-interp");

    this._copyScript().then(function () {
      Zotero.debug("[Trae Lit Interp] Script copied to " + this._scriptDir);
    }.bind(this)).catch(function (e) {
      Zotero.debug("[Trae Lit Interp] Failed to copy script: " + e);
    });

    this._copyUpdateManifest().then(function () {
      Zotero.debug("[Trae Lit Interp] Update manifest copied");
    }.bind(this)).catch(function (e) {
      Zotero.debug("[Trae Lit Interp] Failed to copy update manifest: " + e);
    });

    // 注册设置面板到 Zotero 设置页侧边栏（Zotero 7+ 官方 API，
    // 插件 shutdown 时由 Zotero 自动注销，无需手动 unregister
    if (Zotero.PreferencePanes && Zotero.PreferencePanes.register) {
      Zotero.PreferencePanes.register({
        pluginID: this._pluginID,
        src: "prefs-pane.xhtml",
        scripts: ["prefs-pane.js"],
        id: "trae-lit-interp-prefpane",
        label: "文献解读"
      }).catch(function (e) {
        Zotero.debug("[Trae Lit Interp] Failed to register pref pane: " + e);
      });
    }
  },

  _setDefaults: function () {
    var dataDir = Zotero.DataDirectory.dir;
    var defaultUpdatePath = PathUtils.join(dataDir, "trae-lit-interp", "update.json");
    var defaults = {
      "pythonPath": "python",
      "apiKey": "",
      "apiBase": "https://api.deepseek.com/v1",
      "model": "deepseek-chat",
      "updateSource": defaultUpdatePath
    };
    for (var key in defaults) {
      var prefKey = "extensions.trae-lit-interp." + key;
      if (Zotero.Prefs.get(prefKey, true) === undefined) {
        Zotero.Prefs.set(prefKey, defaults[key], true);
      }
    }
  },

  _copyScript: async function () {
    await IOUtils.makeDirectory(this._scriptDir, { createParent: true });
    var scriptURL = this._rootURI + "scripts/lit_interp_engine.py";
    var response = await fetch(scriptURL);
    var scriptContent = await response.text();
    var scriptPath = PathUtils.join(this._scriptDir, "lit_interp_engine.py");
    var encoded = new TextEncoder().encode(scriptContent);
    await IOUtils.write(scriptPath, encoded);
  },

  _copyUpdateManifest: async function () {
    await IOUtils.makeDirectory(this._scriptDir, { createParent: true });
    var updateURL = this._rootURI + "update.json";
    var response = await fetch(updateURL);
    var updateContent = await response.text();
    var updatePath = PathUtils.join(this._scriptDir, "update.json");
    var encoded = new TextEncoder().encode(updateContent);
    await IOUtils.write(updatePath, encoded);
    Zotero.debug("[Trae Lit Interp] Update manifest at: " + updatePath);
  },

  /******************* Menu Integration *******************/

  addToWindow: function (win) {
    // 使用经过验证的 DOM 注入方式注册右键菜单（zotero-itemmenu）。
    // 不用全局 flag 阻断：每个窗口独立注册，靠 DOM id 去重，天然支持多窗口。
    // （MenuManager 子菜单参数结构在不同版本有差异、易静默失败，故统一走 DOM 方式）
    try {
      this._registerMenuLegacy(win);
    } catch (e) {
      Zotero.debug("[Trae Lit Interp] addToWindow error: " + e);
    }
  },

  _registerMenuAPI: function (win) {
    var self = this;
    try {
      Zotero.MenuManager.registerMenu({
        menuID: "trae-lit-interp",
        pluginID: this._pluginID,
        target: "main/library/item",
        menus: [
          {
            menuType: "submenu",
            label: "文献解读",
            onShowing: function (event, context) {
              var hasItems = context.items && context.items.length > 0;
              context.setVisible(hasItems);
            },
            menus: [
              {
                menuType: "menuitem",
                label: "自动生成解读 (DeepSeek API)",
                onCommand: function (event, context) {
                  self.generateAuto(win);
                }
              },
              {
                menuType: "menuitem",
                label: "导出PDF+提取图表 (手动模式)",
                onCommand: function (event, context) {
                  self.exportPDF(win);
                }
              },
              {
                menuType: "menuitem",
                label: "导入HTML报告",
                onCommand: function (event, context) {
                  self.importHTML(win);
                }
              },
              {
                menuType: "menuitem",
                label: "检查更新...",
                onCommand: function (event, context) {
                  self.checkForUpdates(win);
                }
              },
              {
                menuType: "menuitem",
                label: "设置...",
                onCommand: function (event, context) {
                  self.openSettings(win);
                }
              }
            ]
          }
        ]
      });
      this._menuRegistered = true;
      Zotero.debug("[Trae Lit Interp] Menu registered via MenuManager API");
    } catch (e) {
      Zotero.debug("[Trae Lit Interp] MenuManager registration failed: " + e);
      this._registerMenuLegacy(win);
    }
  },

  _registerMenuLegacy: function (win) {
    var doc = win.document;
    var menuPopup = doc.getElementById("zotero-itemmenu");
    Zotero.debug("[Trae Lit Interp] Legacy: menuPopup " + (menuPopup ? "found" : "NOT FOUND"));
    if (!menuPopup) return;

    // 先移除旧插件实例可能残留的“僵尸”菜单节点（其监听器已随旧沙箱失效），
    // 再重建并挂上当前实例的活监听器，否则菜单可见但点击无响应。
    var stale = doc.getElementById("trae-lit-interp-menu");
    if (stale) stale.remove();

    var menu = doc.createXULElement("menu");
    menu.setAttribute("id", "trae-lit-interp-menu");
    menu.setAttribute("label", "文献解读");

    var subPopup = doc.createXULElement("menupopup");
    subPopup.setAttribute("id", "trae-lit-interp-popup");

    var autoItem = doc.createXULElement("menuitem");
    autoItem.setAttribute("id", "trae-lit-interp-auto");
    autoItem.setAttribute("label", "自动生成解读 (DeepSeek API)");
    autoItem.addEventListener("command", function () {
      TraeLitInterp.generateAuto(win);
    });
    subPopup.appendChild(autoItem);

    var exportItem = doc.createXULElement("menuitem");
    exportItem.setAttribute("id", "trae-lit-interp-export");
    exportItem.setAttribute("label", "导出PDF+提取图表 (手动模式)");
    exportItem.addEventListener("command", function () {
      TraeLitInterp.exportPDF(win);
    });
    subPopup.appendChild(exportItem);

    var importItem = doc.createXULElement("menuitem");
    importItem.setAttribute("id", "trae-lit-interp-import");
    importItem.setAttribute("label", "导入HTML报告");
    importItem.addEventListener("command", function () {
      TraeLitInterp.importHTML(win);
    });
    subPopup.appendChild(importItem);

    var sep = doc.createXULElement("menuseparator");
    subPopup.appendChild(sep);

    var updateItem = doc.createXULElement("menuitem");
    updateItem.setAttribute("id", "trae-lit-interp-update");
    updateItem.setAttribute("label", "检查更新...");
    updateItem.addEventListener("command", function () {
      TraeLitInterp.checkForUpdates(win);
    });
    subPopup.appendChild(updateItem);

    var settingsItem = doc.createXULElement("menuitem");
    settingsItem.setAttribute("id", "trae-lit-interp-settings");
    settingsItem.setAttribute("label", "设置...");
    settingsItem.addEventListener("command", function () {
      TraeLitInterp.openSettings(win);
    });
    subPopup.appendChild(settingsItem);

    menu.appendChild(subPopup);
    menuPopup.appendChild(menu);
    this._menuRegistered = true;
  },

  removeFromWindow: function (win) {
    var doc = win.document;
    var menu = doc.getElementById("trae-lit-interp-menu");
    if (menu) menu.remove();
  },

  removeFromAllWindows: function () {
    var wins = Zotero.getMainWindows();
    if (!wins) return;
    for (var i = 0; i < wins.length; i++) {
      try {
        this.removeFromWindow(wins[i]);
      } catch (e) {
        Zotero.debug("[Trae Lit Interp] removeFromWindow failed: " + e);
      }
    }
  },

  /******************* Core Logic *******************/

  _getSelectedPDF: function (win) {
    var zoteroPane = win.ZoteroPane || Zotero.getActiveZoteroPane();
    var items = zoteroPane.getSelectedItems();
    if (!items || items.length === 0) return null;

    var item = items[0];

    if (item.isRegularItem()) {
      var attachments = item.getAttachments();
      for (var i = 0; i < attachments.length; i++) {
        var att = Zotero.Items.get(attachments[i]);
        if (att.attachmentContentType === "application/pdf") {
          return { parentItem: item, attachment: att };
        }
      }
    }

    if (item.isAttachment() && item.attachmentContentType === "application/pdf") {
      var parent = item.getParent();
      return { parentItem: parent || item, attachment: item };
    }

    return null;
  },

  generateAuto: async function (win) {
    var sel = this._getSelectedPDF(win);
    if (!sel) {
      this._notify(win, "请选中一个包含PDF附件的条目", "error");
      return;
    }

    var apiKey = Zotero.Prefs.get("extensions.trae-lit-interp.apiKey", true);
    if (!apiKey) {
      this._notify(win, "未设置 DeepSeek API Key，请在设置中配置", "error");
      this.openSettings(win);
      return;
    }

    var progressWin = this._showProgress(win, "正在生成文献解读...");

    try {
      var pdfPath = await sel.attachment.getFilePathAsync();

      // 从 Zotero 条目取论文元数据：用于文件命名 + Hero 展示
      var item = sel.parentItem;
      var paperTitle = (item.getField("title") || "").trim();
      var creators = item.getCreators() || [];
      var authorNames = [];
      for (var c = 0; c < creators.length && c < 5; c++) {
        var nm = creators[c].lastName || creators[c].name || "";
        if (nm) authorNames.push(nm);
      }
      var authorsStr = authorNames.join(", ");
      if (creators.length > 5) authorsStr += " 等";
      var journalStr = item.getField("publicationTitle") || item.getField("journalAbbreviation") || "";
      var doiStr = item.getField("DOI") || "";

      // 命名：文献解读_{论文标题}.html（无标题时退回 PDF 文件名）
      var nameBase = paperTitle || PathUtils.filename(pdfPath).replace(/\.pdf$/i, "");
      nameBase = nameBase.replace(/[<>:"/\\|?*]/g, "").replace(/\s+/g, " ").trim();
      if (nameBase.length > 80) nameBase = nameBase.substring(0, 80).trim();
      var outputPath = PathUtils.join(PathUtils.parent(pdfPath), "文献解读_" + nameBase + ".html");

      var args = [
        PathUtils.join(this._scriptDir, "lit_interp_engine.py"),
        "--pdf", pdfPath,
        "--output", outputPath,
        "--mode", "auto",
        "--api-key", apiKey,
        "--api-base", Zotero.Prefs.get("extensions.trae-lit-interp.apiBase", true),
        "--model", Zotero.Prefs.get("extensions.trae-lit-interp.model", true),
        "--title", paperTitle,
        "--authors", authorsStr,
        "--journal", journalStr,
        "--doi", doiStr
      ];

      var exitCode = await this._runPython(args);

      if (exitCode !== 0) {
        progressWin.close();
        var errDetail = this._lastStderr ? "\n\n" + this._lastStderr : "";
        this._notify(win, "Python脚本执行失败 (exit " + exitCode + ")" + errDetail, "error");
        return;
      }

      progressWin.close();

      // 挂为链接附件（文件留在 PDF 同目录）；重复生成时不重复挂
      var alreadyLinked = false;
      var attIDs = sel.parentItem.getAttachments();
      for (var i = 0; i < attIDs.length; i++) {
        var att = await Zotero.Items.getAsync(attIDs[i]);
        if (att && att.linkMode === Zotero.Attachments.LINK_MODE_LINKED_FILE) {
          var attPath = await att.getFilePathAsync();
          if (attPath === outputPath) { alreadyLinked = true; break; }
        }
      }
      if (!alreadyLinked) {
        await Zotero.Attachments.linkFromFile({
          file: outputPath,
          parentItemID: sel.parentItem.id
        });
      }
      // 资源管理器弹出定位，方便直接双击打开
      this._openFolder(outputPath);
      this._notify(win, "文献解读已生成: " + outputPath, "success");
    } catch (e) {
      progressWin.close();
      Zotero.debug("[Trae Lit Interp] Error: " + e);
      this._notify(win, "生成失败: " + e.message, "error");
    }
  },

  exportPDF: async function (win) {
    var sel = this._getSelectedPDF(win);
    if (!sel) {
      this._notify(win, "请选中一个包含PDF附件的条目", "error");
      return;
    }

    var progressWin = this._showProgress(win, "正在提取PDF内容和图表...");

    try {
      var pdfPath = await sel.attachment.getFilePathAsync();
      var workDir = PathUtils.join(this._scriptDir, "work_" + Date.now());
      await IOUtils.makeDirectory(workDir, { createParent: true });

      var outputPath = PathUtils.join(workDir, "template.html");
      var promptPath = PathUtils.join(workDir, "prompt.txt");

      var args = [
        PathUtils.join(this._scriptDir, "lit_interp_engine.py"),
        "--pdf", pdfPath,
        "--output", outputPath,
        "--mode", "manual",
        "--prompt-file", promptPath
      ];

      var exitCode = await this._runPython(args);
      progressWin.close();

      if (exitCode !== 0) {
        this._notify(win, "Python脚本执行失败 (exit " + exitCode + ")", "error");
        return;
      }

      this._openFolder(workDir);
      this._notify(win, "PDF内容和图表已提取到: " + workDir, "success");
    } catch (e) {
      progressWin.close();
      this._notify(win, "导出失败: " + e.message, "error");
    }
  },

  importHTML: async function (win) {
    var sel = this._getSelectedPDF(win);
    var parentItem = sel ? sel.parentItem : null;

    if (!parentItem) {
      var zoteroPane = win.ZoteroPane || Zotero.getActiveZoteroPane();
      var items = zoteroPane.getSelectedItems();
      if (items && items.length > 0) {
        parentItem = items[0];
      }
    }

    if (!parentItem) {
      this._notify(win, "请先选中一个文献条目", "error");
      return;
    }

    // 文件选择框：用 Zotero 10 原生 filePicker.mjs（参考 zotero-plugin-toolkit 的写法）
    var FilePickerBackend = ChromeUtils.importESModule(
      "chrome://zotero/content/modules/filePicker.mjs").FilePicker;
    var fp = new FilePickerBackend();
    fp.init(win, "选择文献解读HTML文件", fp.modeOpen);
    fp.appendFilter("HTML", "*.html;*.htm");

    var result = await fp.show();
    if (result !== fp.returnOK) return;

    try {
      var picked = fp.file;
      var pickedPath = (typeof picked === "string") ? picked : picked.path;
      var htmlContent = await this._readFile(pickedPath);
      await this._createNote(parentItem, htmlContent, "文献解读");
      this._notify(win, "HTML报告已导入为笔记", "success");
    } catch (e) {
      this._notify(win, "导入失败: " + e.message, "error");
    }
  },

  /******************* Settings Dialog *******************/

  openSettings: function (win) {
    // 直接跳转到 Zotero 设置页中本插件的设置面板（官方 API）
    try {
      Zotero.Utilities.openPreferences("trae-lit-interp-prefpane");
    } catch (e) {
      Zotero.debug("[Trae Lit Interp] openPreferences failed: " + e);
    }
  },

  /******************* Update Check *******************/

  checkForUpdates: async function (win) {
    var updateSource = Zotero.Prefs.get("extensions.trae-lit-interp.updateSource", true) || "";
    if (!updateSource) {
      var dataDir = Zotero.DataDirectory.dir;
      updateSource = PathUtils.join(dataDir, "trae-lit-interp", "update.json");
    }

    try {
      var fileExists = await IOUtils.exists(updateSource);
      if (!fileExists) {
        this._notify(win, "更新清单不存在: " + updateSource + "\n请在设置中配置正确路径", "error");
        return;
      }

      var content = await IOUtils.readUTF8(updateSource);
      var updateData = JSON.parse(content);

      var addonInfo = updateData.addons && updateData.addons[this._pluginID];
      if (!addonInfo || !addonInfo.updates || addonInfo.updates.length === 0) {
        this._notify(win, "当前版本 " + this._version + " 已是最新", "success");
        return;
      }

      var latest = addonInfo.updates[0];
      var latestVersion = latest.version;

      if (this._compareVersions(latestVersion, this._version) > 0) {
        // 窗口原生 confirm（Zotero 10 已移除旧 XPCOM prompt-service）
        var doUpdate = win.confirm(
          "发现新版本\n\n当前版本: " + this._version + "\n最新版本: " + latestVersion +
          "\n\n更新链接:\n" + latest.update_link +
          "\n\n点击确定打开下载页面");
        if (doUpdate && latest.update_link) {
          Zotero.launchURL(latest.update_link);
        }
      } else {
        this._notify(win, "当前版本 " + this._version + " 已是最新", "success");
      }
    } catch (e) {
      this._notify(win, "检查更新失败: " + e.message, "error");
    }
  },

  _compareVersions: function (a, b) {
    var partsA = a.split(".");
    var partsB = b.split(".");
    var len = Math.max(partsA.length, partsB.length);
    for (var i = 0; i < len; i++) {
      var numA = parseInt(partsA[i] || "0", 10);
      var numB = parseInt(partsB[i] || "0", 10);
      if (numA > numB) return 1;
      if (numA < numB) return -1;
    }
    return 0;
  },

  /******************* Utilities *******************/

  _runPython: async function (args) {
    var pythonPath = Zotero.Prefs.get("extensions.trae-lit-interp.pythonPath", true) || "python";

    // 用 Firefox 平台 Subprocess 启动进程（Zotero 10 下 nsIProcess 旧 XPCOM 写法不可靠）。
    // 注意：Subprocess 不搜索 PATH，command 必须是绝对路径，
    // 所以相对命令（如 "python"）先用 where.exe 解析成全路径。
    var { Subprocess } = ChromeUtils.importESModule(
      "resource://gre/modules/Subprocess.sys.mjs");
    var command = pythonPath;
    var isAbs = /^[A-Za-z]:[\\\/]/.test(command) || command.startsWith("\\\\") || command.startsWith("/");
    if (!isAbs) {
      try {
        var where = await Subprocess.call({
          command: "C:\\Windows\\System32\\where.exe",
          arguments: [command]
        });
        var out = await where.stdout.read();
        var first = new TextDecoder().decode(out).trim().split(/\r?\n/)[0];
        await where.wait();
        if (first) command = first;
      } catch (e) {
        Zotero.debug("[Trae Lit Interp] where.exe resolve failed: " + e);
      }
    }
    var proc = await Subprocess.call({
      command: command,
      arguments: args,
      stdout: "pipe",
      stderr: "pipe"
    });
    // exitCode 只是普通属性（初始 null），真正的退出码要等 wait() 返回
    var result = await proc.wait();
    // 读取 stderr，失败时用于展示具体错误原因（如 API error / 缺参数）
    var stderrText = "";
    try {
      var errBytes = await proc.stderr.read();
      stderrText = new TextDecoder().decode(errBytes);
    } catch (e) {
      stderrText = "";
    }
    this._lastStderr = stderrText.trim();
    return result.exitCode;
  },

  _readFile: async function (path) {
    var bytes = await IOUtils.read(path);
    return new TextDecoder().decode(bytes);
  },

  _createNote: async function (parentItem, htmlContent, title) {
    var note = new Zotero.Item("note");
    note.libraryID = parentItem.libraryID;
    note.parentID = parentItem.id;
    note.setNote('<h1>' + title + '</h1>' + htmlContent);
    await note.saveTx();
    return note;
  },

  _showProgress: function (win, message) {
    var progressWin = new Zotero.ProgressWindow({ closeOnClick: false });
    progressWin.changeHeadline("Trae 文献解读");
    progressWin.addDescription(message);
    progressWin.show();
    return progressWin;
  },

  _notify: function (win, message, type) {
    if (type === "error") {
      // Zotero 10 (FF140) 已移除旧 XPCOM prompt-service，
      // 改用窗口原生 alert（参考 Actions and Tags 等正常插件的写法）
      try {
        (win || Zotero.getMainWindow()).alert(message);
      } catch (e) {
        Zotero.debug("[Trae Lit Interp] alert failed: " + e);
      }
    } else {
      var progressWin = new Zotero.ProgressWindow({ closeOnClick: true });
      progressWin.changeHeadline("Trae 文献解读");
      progressWin.addDescription(message);
      progressWin.startCloseTimer(4000);
    }
  },

  _openFolder: function (path) {
    // 参考 Better Notes 的写法：Zotero.File.reveal 在资源管理器中显示
    try {
      Zotero.File.reveal(path);
    } catch (e) {
      Zotero.debug("[Trae Lit Interp] Cannot open folder: " + e);
    }
  }
};

/******************* Bootstrap Lifecycle (Zotero 7+) *******************/

function install(data, reason) {
  Zotero.debug("[Trae Lit Interp] Installed " + (data ? data.version : "?"));
}

async function startup({ id, version, resourceURI, rootURI }, reason) {
  // 关键！等待 Zotero 核心完全初始化
  await Zotero.initializationPromise;
  Zotero.debug("[Trae Lit Interp] Starting up " + version);

  // rootURI 在 Zotero 7+ 可用，回退到 resourceURI（兼容旧版本）
  if (!rootURI && resourceURI) {
    rootURI = resourceURI.spec;
  }

  try {
    TraeLitInterp.init({ id: id, version: version, rootURI: rootURI });

    // 主动对所有「已打开」的主窗口注册菜单。
    // 不单纯依赖 onMainWindowLoad 钩子（插件启动时窗口可能已加载完毕，钩子不再触发），
    // 这里枚举已有窗口 + onMainWindowLoad 处理未来窗口，双保险。
    var wins = Zotero.getMainWindows();
    Zotero.debug("[Trae Lit Interp] getMainWindows count: " + (wins ? wins.length : 0));
    if (wins) {
      for (var i = 0; i < wins.length; i++) {
        try {
          TraeLitInterp.addToWindow(wins[i]);
        } catch (e) {
          Zotero.debug("[Trae Lit Interp] addToWindow failed for window " + i + ": " + e);
        }
      }
    }
  } catch (e) {
    Zotero.debug("[Trae Lit Interp] Startup error: " + e);
  }
}

async function onMainWindowLoad({ window }, reason) {
  try {
    TraeLitInterp.addToWindow(window);
  } catch (e) {
    Zotero.debug("[Trae Lit Interp] onMainWindowLoad error: " + e);
  }
}

async function onMainWindowUnload({ window }, reason) {
  try {
    TraeLitInterp.removeFromWindow(window);
  } catch (e) {
    Zotero.debug("[Trae Lit Interp] onMainWindowUnload error: " + e);
  }
}

function shutdown({ id, version, resourceURI, rootURI }, reason) {
  // APP_SHUTDOWN 时不做清理，避免延迟 Zotero 退出
  if (reason === APP_SHUTDOWN) {
    return;
  }
  Zotero.debug("[Trae Lit Interp] Shutting down");
  TraeLitInterp.removeFromAllWindows();
  if (TraeLitInterp._menuRegistered && Zotero.MenuManager && Zotero.MenuManager.unregisterMenu) {
    try {
      Zotero.MenuManager.unregisterMenu("trae-lit-interp");
    } catch (e) {
      Zotero.debug("[Trae Lit Interp] unregisterMenu failed: " + e);
    }
  }
  TraeLitInterp._initialized = false;
  TraeLitInterp._menuRegistered = false;
}

function uninstall(data, reason) {
  TraeLitInterp.removeFromAllWindows();
  Zotero.debug("[Trae Lit Interp] Uninstalled");
}
