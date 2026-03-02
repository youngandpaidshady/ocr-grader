/**
 * QSI Smart Grader — Enhancement Layer
 * Adds: multi-class picker, image compression, contextual guides,
 * tabbed results, duplicate detection, AI safety net, smart assistant
 */

// ═══════════════════════════════════════════════
//  SESSION CLASS TRACKING (with localStorage persistence)
// ═══════════════════════════════════════════════
const sessionClasses = new Set(
    JSON.parse(localStorage.getItem('qsi_sessionClasses') || '[]')
);

function addToSessionClasses(className) {
    if (className && className.trim()) {
        sessionClasses.add(className.trim());
        localStorage.setItem('qsi_sessionClasses', JSON.stringify([...sessionClasses]));
    }
}

function clearSessionData() {
    sessionClasses.clear();
    selectedClasses = [];
    localStorage.removeItem('qsi_sessionClasses');
    localStorage.removeItem('qsi_lastSubject');
    localStorage.removeItem('qsi_lastAssessment');
    // Clear global state
    if (typeof extractedData !== 'undefined') window.extractedData = [];
    if (typeof imagesBatch !== 'undefined') window.imagesBatch = [];
    if (typeof window._excelRecords !== 'undefined') window._excelRecords = null;
    sheetsData = null;
    activeTab = null;
    assistantContext = {};
    assistantHistory = [];
    console.log('[QSI] Session data cleared');
}

// ═══════════════════════════════════════════════
//  IMAGE COMPRESSION (Workstream 2)
// ═══════════════════════════════════════════════
function compressImage(base64Str, maxWidth = 1200, quality = 0.7) {
    return new Promise((resolve) => {
        const img = new Image();
        img.onload = () => {
            const canvas = document.createElement('canvas');
            let width = img.width;
            let height = img.height;

            if (width > maxWidth) {
                height = Math.round((height * maxWidth) / width);
                width = maxWidth;
            }

            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, width, height);
            const compressed = canvas.toDataURL('image/jpeg', quality);
            resolve(compressed);
        };
        img.src = base64Str;
    });
}

async function compressBatch(images) {
    return Promise.all(images.map(img => compressImage(img)));
}

// ═══════════════════════════════════════════════
//  CONTEXTUAL GUIDE SYSTEM (Workstream 7)
// ═══════════════════════════════════════════════
const guideMessages = {
    'landing': {
        message: '👋 Welcome! If you\'ve used this before, tap <strong>"I Have My Excel"</strong> to pick up where you left off. Starting fresh? Tap <strong>"Start Fresh"</strong> and add your class list to get going.',
        target: '#landing-phase',
        position: 'fixed-top'
    },
    'class-picker': {
        message: '📋 Pick the class(es) you\'re grading. <strong>Make sure your class list is complete</strong> — the AI matches scanned names against it. You can always add missing students later from the class picker.',
        target: '#class-picker-container',
        position: 'below'
    },
    'subject': {
        message: '📘 What subject is this for? This keeps each subject\'s scores separate in your file.',
        target: '#subject-name',
        position: 'below'
    },
    'assessment': {
        message: '📝 What kind of test is this? e.g. 1st CA, 2nd CA, Exam. This labels the score column.',
        target: '#assessment-type',
        position: 'fixed-top'
    },
    'camera': {
        message: '📸 Point your camera at the student\'s graded paper and tap <strong>Capture</strong>. If auto-capture feels jumpy, just turn it off and snap manually.',
        target: '#btn-capture',
        position: 'above'
    },
    'review': {
        message: '✅ Double-check each name and score. Tap any field to fix mistakes, then hit <strong>"Save"</strong> when it all looks right.',
        target: '#review-section',
        position: 'top'
    },
    'after-save': {
        message: '🎉 Grades saved! Tap <strong>"Download"</strong> to get your updated Excel file. Keep that file safe — you\'ll need it next time!',
        target: '#btn-download-sheet',
        position: 'above'
    }
};

function showGuide(guideKey) {
    const guide = guideMessages[guideKey];
    if (!guide) return;

    // Remove any existing guide
    const existing = document.querySelector('.guide-popup');
    if (existing) existing.remove();

    const popup = document.createElement('div');
    popup.className = 'guide-popup';
    popup.innerHTML = `
        <div class="guide-popup-content flex items-start gap-3 relative">
            <i class="fa-solid fa-circle-info text-blue-500 text-xl mt-0.5 shrink-0"></i>
            <div class="guide-popup-message flex-1 pr-4 text-sm font-medium text-white/90">
                ${guide.message}
            </div>
            <button class="guide-popup-dismiss absolute top-3 right-3 text-white/40 hover:text-white transition-colors" onclick="dismissGuide('${guideKey}', this)">
                <i class="fa-solid fa-xmark"></i>
            </button>
        </div>
    `;

    document.body.appendChild(popup);

    // Wait for render to get dimensions
    requestAnimationFrame(() => {
        const target = document.querySelector(guide.target);
        if (target) {
            const rect = target.getBoundingClientRect();
            const scrollY = window.scrollY;
            const popupWidth = popup.offsetWidth || 280;
            const popupHeight = popup.offsetHeight || 100;

            if (guide.position === 'fixed-top') {
                popup.style.top = (scrollY + 80) + 'px'; // Below header
                popup.style.left = '50%';
                popup.style.marginLeft = -(popupWidth / 2) + 'px';
                return; // don't add pulse to target
            } else if (guide.position === 'center') {
                popup.style.top = (rect.top + scrollY + rect.height / 2 - popupHeight / 2) + 'px';
                popup.style.left = '50%';
                popup.style.marginLeft = -(popupWidth / 2) + 'px';
            } else if (guide.position === 'below') {
                popup.style.top = (rect.bottom + scrollY + 12) + 'px';
                popup.style.left = Math.max(16, rect.left) + 'px';
            } else if (guide.position === 'above') {
                popup.style.bottom = (window.innerHeight - rect.top - scrollY + 12) + 'px';
                popup.style.left = Math.max(16, rect.left) + 'px';
            } else {
                popup.style.top = (rect.top + scrollY) + 'px';
                popup.style.left = '50%';
                popup.style.marginLeft = -(popupWidth / 2) + 'px';
            }

            // Add pulsing indicator to target
            target.classList.add('guide-pulse');
        }
    });

    // Auto-dismiss after 10s
    setTimeout(() => {
        if (popup.parentNode) dismissGuide(guideKey, popup.querySelector('.guide-popup-dismiss'));
    }, 10000);
}

function dismissGuide(guideKey, btn) {
    const popup = btn ? btn.closest('.guide-popup') : document.querySelector('.guide-popup');
    if (popup) {
        popup.classList.add('guide-popup-exit');
        setTimeout(() => popup.remove(), 300);
    }
    // Remove pulse from target
    const guide = guideMessages[guideKey];
    if (guide) {
        const target = document.querySelector(guide.target);
        if (target) target.classList.remove('guide-pulse');
    }
}

// ═══════════════════════════════════════════════
//  MULTI-CLASS CHIP PICKER (Workstream 3)
// ═══════════════════════════════════════════════
let selectedClasses = [];

function initClassPicker() {
    const container = document.getElementById('class-picker-container');
    if (!container) { console.warn('[QSI] class-picker-container not found'); return; }

    // Fetch classes from API
    fetch('/api/classes')
        .then(r => r.json())
        .then(classes => {
            console.log('[QSI] Classes loaded:', classes);
            renderClassChips(container, classes);
        })
        .catch((err) => {
            console.error('[QSI] Failed to load classes:', err);
            container.innerHTML = '<p class="text-muted-foreground text-sm">Could not load classes.</p>';
        });
}
window.initClassPicker = initClassPicker;

function renderClassChips(container, classes) {
    const chipGrid = container.querySelector('.chip-grid') || container;
    // Keep the add button, clear only chips
    const chips = chipGrid.querySelectorAll('.class-chip');
    chips.forEach(c => c.remove());

    classes.forEach(cls => {
        const isSelected = selectedClasses.includes(cls.name);
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = `class-chip ${isSelected ? 'class-chip-active' : ''}`;
        chip.dataset.class = cls.name;
        chip.innerHTML = `
            <i class="fa-solid ${isSelected ? 'fa-check-circle' : 'fa-circle'} mr-1.5 text-xs"></i>
            <span>${cls.name}</span>
        `;
        chip.addEventListener('click', () => toggleClassChip(chip, cls.name));

        const addBtn = chipGrid.querySelector('#btn-add-class-chip');
        if (addBtn) {
            chipGrid.insertBefore(chip, addBtn);
        } else {
            chipGrid.appendChild(chip);
        }
    });

    // Also populate the old target-class dropdown for backward compatibility
    const oldSelect = document.getElementById('target-class');
    if (oldSelect) {
        const currentVal = oldSelect.value;
        // Clear and repopulate
        oldSelect.innerHTML = '<option value="" disabled selected class="bg-card">-- Select a Class --</option>';
        classes.forEach(cls => {
            const opt = document.createElement('option');
            opt.value = cls.name;
            opt.textContent = cls.name;
            opt.className = 'bg-card';
            oldSelect.appendChild(opt);
        });
        if (currentVal) oldSelect.value = currentVal;
    }
}

function toggleClassChip(chip, className) {
    const idx = selectedClasses.indexOf(className);
    const subjectSelect = document.getElementById('subject-name');

    if (idx > -1) {
        selectedClasses.splice(idx, 1);
        chip.classList.remove('class-chip-active');
        chip.querySelector('i').className = 'fa-solid fa-circle mr-1.5 text-xs';
        sessionClasses.delete(className);
    } else {
        selectedClasses.push(className);
        chip.classList.add('class-chip-active');
        chip.querySelector('i').className = 'fa-solid fa-check-circle mr-1.5 text-xs';
        addToSessionClasses(className);

    }


    // Sync to old dropdown (use first selected class)
    const oldSelect = document.getElementById('target-class');
    if (oldSelect && selectedClasses.length > 0) {
        oldSelect.value = selectedClasses[0];
        oldSelect.dispatchEvent(new Event('change'));
    }

    // Show enrollment prompt if subject is already selected
    // (Removed — enrollment replaced with General/Elective toggle)
}


// ═══════════════════════════════════════════════
//  DUPLICATE DETECTION (Workstream 4)
// ═══════════════════════════════════════════════
function checkForDuplicate(newItem, extractedData) {
    if (!newItem.name || !newItem.name.trim()) return -1;

    const newName = newItem.name.trim().toLowerCase();
    const newClass = (newItem.class || '').trim().toLowerCase();

    for (let i = 0; i < extractedData.length; i++) {
        const existingName = (extractedData[i].name || '').trim().toLowerCase();
        const existingClass = (extractedData[i].class || '').trim().toLowerCase();

        if (existingName === newName && existingClass === newClass) {
            return i;
        }
    }
    return -1;
}

function showDuplicatePrompt(existingIdx, newItem, extractedData, tbody, assessmentType) {
    const existingItem = extractedData[existingIdx];
    const existingScore = existingItem.score || '?';
    const newScore = newItem.score || '?';
    const studentName = newItem.name || 'Unknown';

    // Find the row in the table
    const rows = tbody.querySelectorAll('tr');
    const targetRow = rows[existingIdx];
    if (!targetRow) return;

    // Add pulse effect
    targetRow.classList.add('duplicate-pulse');

    // Create inline prompt
    const promptRow = document.createElement('tr');
    promptRow.className = 'duplicate-prompt-row';
    promptRow.innerHTML = `
        <td colspan="100%" class="p-0">
            <div class="duplicate-prompt-card animate-fade-in-up">
                <div class="flex items-start gap-3 p-4">
                    <i class="fa-solid fa-copy text-amber-400 text-lg mt-0.5"></i>
                    <div class="flex-1">
                        <p class="text-sm font-bold text-white mb-1">📋 ${studentName}'s ${assessmentType || 'score'} was already scanned</p>
                        <p class="text-xs text-amber-200/70">
                            Previous scan: <strong class="text-white">${existingScore}</strong> &nbsp;→&nbsp; 
                            New scan: <strong class="text-emerald-400">${newScore}</strong>
                        </p>
                    </div>
                </div>
                <div class="flex gap-2 px-4 pb-4">
                    <button onclick="resolveDuplicate(${existingIdx}, 'keep_old', this)"
                        class="flex-1 px-3 py-2 bg-white/5 hover:bg-white/10 text-white text-xs font-bold rounded-lg transition-all border border-white/10">
                        Keep ${existingScore} — first was correct
                    </button>
                    <button onclick="resolveDuplicate(${existingIdx}, 'use_new', this, '${newScore.toString().replace("'", "\\'")}')"
                        class="flex-1 px-3 py-2 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 text-xs font-bold rounded-lg transition-all border border-emerald-500/30">
                        Use ${newScore} — this scan is better
                    </button>
                    <button onclick="resolveDuplicate(${existingIdx}, 'fix_name', this)"
                        class="flex-1 px-3 py-2 bg-amber-500/20 hover:bg-amber-500/30 text-amber-400 text-xs font-bold rounded-lg transition-all border border-amber-500/30">
                        Wrong name — let me fix
                    </button>
                </div>
            </div>
        </td>
    `;

    // Insert after the target row
    targetRow.after(promptRow);

    // Store pending new item data
    promptRow.dataset.newItem = JSON.stringify(newItem);
}

function resolveDuplicate(existingIdx, action, btn, newScore) {
    const promptRow = btn.closest('.duplicate-prompt-row');
    const tbody = document.getElementById('results-body') || document.querySelector('#review-section tbody');
    const rows = tbody ? tbody.querySelectorAll('tr:not(.duplicate-prompt-row)') : [];
    const targetRow = rows[existingIdx];

    if (action === 'keep_old') {
        // Do nothing — keep existing score
    } else if (action === 'use_new') {
        // Update the existing row's score
        if (typeof extractedData !== 'undefined' && extractedData[existingIdx]) {
            extractedData[existingIdx].score = newScore;
        }
        const scoreInput = targetRow?.querySelector('input[data-field="score"]');
        if (scoreInput) {
            scoreInput.value = newScore;
            scoreInput.classList.add('ring-2', 'ring-emerald-500', 'bg-emerald-500/20');
            setTimeout(() => scoreInput.classList.remove('ring-2', 'ring-emerald-500', 'bg-emerald-500/20'), 2000);
        }
    } else if (action === 'fix_name') {
        // Focus the name input for editing
        const nameInput = targetRow?.querySelector('input[data-field="name"]');
        if (nameInput) {
            nameInput.focus();
            nameInput.select();
            nameInput.classList.add('ring-2', 'ring-amber-500');
            setTimeout(() => nameInput.classList.remove('ring-2', 'ring-amber-500'), 3000);
        }
    }

    // Clean up
    if (targetRow) targetRow.classList.remove('duplicate-pulse');
    if (promptRow) {
        promptRow.classList.add('opacity-0');
        setTimeout(() => promptRow.remove(), 300);
    }
}

// ═══════════════════════════════════════════════
//  TABBED RESULTS — Level-Grouped (Workstream 5 v2)
// ═══════════════════════════════════════════════
let sheetsData = null;
let activeTab = null;

function parseClassLevel(className) {
    const cleaned = (className || '').replace(/\./g, '').toUpperCase().trim().replace(/\s+/g, ' ');
    const match = cleaned.match(/^([A-Z]+)\s*(\d+)\s*(.*)$/);
    if (match) {
        return { level: match[1] + match[2], arm: match[3].trim() || '_default', normalized: cleaned };
    }
    return { level: cleaned || 'OTHER', arm: '_default', normalized: className };
}

function renderResultsTabs(sheets, downloads) {
    sheetsData = sheets;
    const analyticsSection = document.getElementById('analytics-section');
    if (!analyticsSection) return;

    // Remove old tab bar and content if exists
    const oldTabBar = analyticsSection.querySelector('.results-tab-bar');
    if (oldTabBar) oldTabBar.remove();
    const oldTable = analyticsSection.querySelector('.results-tab-content');
    if (oldTable) oldTable.remove();

    const sheetNames = Object.keys(sheets);
    if (sheetNames.length === 0) return;

    // Group sheets by level
    const levelGroups = {};
    sheetNames.forEach(name => {
        const sheet = sheets[name];
        const level = sheet.level || parseClassLevel(sheet.class || name).level;
        if (!levelGroups[level]) levelGroups[level] = [];
        levelGroups[level].push({ name, sheet });
    });

    // Create level-grouped tab bar
    const tabBar = document.createElement('div');
    tabBar.className = 'results-tab-bar';

    let tabsHTML = '<div class="results-tabs-scroll">';
    let isFirst = true;
    Object.entries(levelGroups).forEach(([level, items]) => {
        if (Object.keys(levelGroups).length > 1) {
            tabsHTML += `<span class="results-level-label">${level}</span>`;
        }
        items.forEach(({ name, sheet }) => {
            const activeClass = isFirst ? 'results-tab-active' : '';
            const escapedName = name.replace(/'/g, "\\'");
            tabsHTML += `<button class="results-tab ${activeClass}" 
                data-sheet="${name}" onclick="switchResultsTab('${escapedName}', this)">
                <span class="results-tab-class">${sheet.class || name}</span>
                <span class="results-tab-subject">${sheet.subject || ''}</span>
            </button>`;
            isFirst = false;
        });
    });
    tabsHTML += '</div>';

    // Add download buttons per level if multiple files
    if (downloads && downloads.length > 1) {
        tabsHTML += '<div class="results-download-row">';
        downloads.forEach(dl => {
            tabsHTML += `<a href="${dl.url}&subject=${encodeURIComponent(sheets[Object.keys(sheets)[0]]?.subject || '')}" 
                class="results-download-btn" download>
                <i class="fa-solid fa-download mr-1.5"></i>${dl.filename}
            </a>`;
        });
        tabsHTML += '</div>';
    }

    tabBar.innerHTML = tabsHTML;

    // Insert after the heading
    const heading = analyticsSection.querySelector('.flex.flex-col.mb-8');
    if (heading) {
        heading.after(tabBar);
    } else {
        analyticsSection.prepend(tabBar);
    }

    // Create table container
    const tableContainer = document.createElement('div');
    tableContainer.className = 'results-tab-content mb-8';
    tabBar.after(tableContainer);

    // Show first tab
    switchResultsTab(sheetNames[0]);
}

function switchResultsTab(sheetName, btn) {
    if (!sheetsData || !sheetsData[sheetName]) return;
    activeTab = sheetName;

    const sheet = sheetsData[sheetName];
    const container = document.querySelector('.results-tab-content');
    if (!container) return;

    // Update active tab styling
    document.querySelectorAll('.results-tab').forEach(t => t.classList.remove('results-tab-active'));
    if (btn) {
        btn.classList.add('results-tab-active');
    } else {
        document.querySelector(`.results-tab[data-sheet="${sheetName}"]`)?.classList.add('results-tab-active');
    }

    // Update heading
    const heading = document.querySelector('#analytics-section h2');
    if (heading) heading.textContent = `${sheet.class} — ${sheet.subject}`;

    // Build table — show only first 5 rows, rest collapsible
    if (!sheet.rows || sheet.rows.length === 0) {
        container.innerHTML = '<p class="text-muted-foreground text-sm text-center py-8">No data for this class yet.</p>';
        return;
    }

    const columns = sheet.columns || Object.keys(sheet.rows[0] || {});
    const previewCount = 5;
    const hasMore = sheet.rows.length > previewCount;

    const buildRows = (rows) => rows.map((row, i) => `
        <tr class="border-b border-white/5 hover:bg-white/5 transition-colors ${i % 2 === 0 ? 'bg-black/10' : ''}">
            ${columns.map(col => `<td class="px-4 py-3 text-white font-medium whitespace-nowrap">${row[col] ?? ''}</td>`).join('')}
        </tr>
    `).join('');

    container.innerHTML = `
        <div class="overflow-x-auto rounded-xl border border-white/5">
            <table class="w-full text-sm">
                <thead>
                    <tr class="border-b border-white/10 bg-black/30">
                        ${columns.map(col => `<th class="px-4 py-3 text-left text-xs font-bold text-muted-foreground uppercase tracking-widest whitespace-nowrap">${col}</th>`).join('')}
                    </tr>
                </thead>
                <tbody id="results-table-preview">
                    ${buildRows(sheet.rows.slice(0, previewCount))}
                </tbody>
                ${hasMore ? `<tbody id="results-table-full" class="hidden">
                    ${buildRows(sheet.rows)}
                </tbody>` : ''}
            </table>
        </div>
        ${hasMore ? `<button id="results-table-toggle" class="mt-3 w-full text-center text-xs font-bold text-primary hover:text-white py-3 rounded-lg border border-white/5 hover:border-primary/30 bg-black/20 hover:bg-primary/10 transition-all">
            ▼ Show all ${sheet.rows.length} students
        </button>` : ''}
    `;

    // Wire up toggle
    if (hasMore) {
        const toggleBtn = container.querySelector('#results-table-toggle');
        const previewBody = container.querySelector('#results-table-preview');
        const fullBody = container.querySelector('#results-table-full');
        if (toggleBtn && previewBody && fullBody) {
            toggleBtn.addEventListener('click', function () {
                const isExpanded = !fullBody.classList.contains('hidden');
                previewBody.classList.toggle('hidden');
                fullBody.classList.toggle('hidden');
                this.textContent = isExpanded
                    ? '▼ Show all ' + sheet.rows.length + ' students'
                    : '▲ Collapse list';
            });
        }
    }
}

// ═══════════════════════════════════════════════
//  AI SAFETY NET — Frontend Wiring (Workstream 6)
// ═══════════════════════════════════════════════
async function aiResolve(situation, context) {
    try {
        const response = await fetch('/api/ai-resolve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ situation, context })
        });
        const data = await response.json();
        return data.success ? data.result : null;
    } catch (e) {
        console.error('AI resolve failed:', e);
        return null;
    }
}

async function handleErrorWithAI(error, action) {
    const result = await aiResolve('error_explain', {
        error: error.message || String(error),
        action: action
    });
    if (result) {
        showToast(result.friendly_message, result.can_retry ? 'warning' : 'error', 6000);
        return result;
    }
    return null;
}

// ═══════════════════════════════════════════════
//  SMART EXCEL ASSISTANT (Workstream 8)
// ═══════════════════════════════════════════════
let assistantContext = {};
let assistantHistory = [];

function openSmartAssistant(parsedData) {
    const modal = document.getElementById('smart-assistant-modal');
    if (!modal) return;

    // Hide the FAB while the modal is open
    const fab = document.getElementById('smart-assistant-fab');
    if (fab) fab.classList.add('hidden');

    // Build context — either from Excel upload data or from the current UI state
    if (parsedData && parsedData.classes) {
        assistantContext = {
            classes: parsedData.classes || [],
            subjects: parsedData.subjects || [],
            students: parsedData.studentCount || 0,
            assessments: parsedData.assessments || []
        };
    } else {
        // Build from current UI state
        const currentClasses = typeof selectedClasses !== 'undefined' ? [...selectedClasses] : [];
        const subjSelect = document.getElementById('subject-name');
        const currentSubject = subjSelect ? (subjSelect.value === 'custom'
            ? document.getElementById('custom-subject')?.value?.trim()
            : subjSelect.value) : '';
        const assessSelect = document.getElementById('assessment-type');
        const currentAssessment = assessSelect ? assessSelect.value : '';

        assistantContext = {
            classes: currentClasses,
            subjects: currentSubject ? [currentSubject] : [],
            students: 0,
            assessments: currentAssessment ? [currentAssessment] : []
        };
    }

    // Detect current screen for context-aware welcome
    let currentScreen = 'landing';
    if (!document.getElementById('start-section')?.classList.contains('hidden')) currentScreen = 'setup';
    else if (!document.getElementById('capture-section')?.classList.contains('hidden')) currentScreen = 'capture';
    else if (!document.getElementById('review-section')?.classList.contains('hidden')) currentScreen = 'review';
    else if (!document.getElementById('analytics-section')?.classList.contains('hidden')) currentScreen = 'results';

    // Build context-aware welcome summary
    const summaryEl = modal.querySelector('#assistant-summary');
    if (summaryEl) {
        if (parsedData && parsedData.classes) {
            // Excel upload context — show the file summary
            const classesList = parsedData.classes?.join(', ') || 'Unknown';
            const assessments = parsedData.assessments?.join(', ') || 'None detected';
            summaryEl.innerHTML = `
                <div class="flex items-start gap-3 p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-xl">
                    <i class="fa-solid fa-file-excel text-emerald-400 text-2xl"></i>
                    <div>
                        <p class="text-sm font-bold text-white">Your file looks good!</p>
                        <p class="text-xs text-emerald-300/70 mt-1">
                            Found <strong>${parsedData.studentCount || 0} students</strong> in 
                            <strong>${classesList}</strong> with <strong>${assessments}</strong> scores.
                        </p>
                    </div>
                </div>
            `;
        } else {
            // General context — show a friendly, screen-aware greeting
            const welcomeMessages = {
                'landing': "Hey there! \ud83d\udc4b I can help you set up a grading session, check your results, or add students to your class list. Just ask!",
                'setup': "Looks like you're setting things up. Need help picking the right assessment type, or want to add a missing student?",
                'capture': "You're scanning scripts \u2014 nice! If a student's name isn't coming up right, I can help fix it or add them to the list.",
                'review': "Reviewing your results? I can help correct names or scores, or add someone who's missing.",
                'results': "Here are your results! Want to download your Excel, check rankings, or start grading the next test?"
            };
            const msg = welcomeMessages[currentScreen] || welcomeMessages['landing'];
            summaryEl.innerHTML = `
                <div class="flex items-start gap-3 p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-xl">
                    <i class="fa-solid fa-wand-magic-sparkles text-indigo-400 text-xl"></i>
                    <div>
                        <p class="text-sm text-white/90 leading-relaxed">${msg}</p>
                    </div>
                </div>
            `;
        }
    }

    // Clear chat history
    assistantHistory = [];
    const chatEl = modal.querySelector('#assistant-chat');
    if (chatEl) chatEl.innerHTML = '';

    modal.classList.remove('hidden');
    modal.classList.add('flex');

    // Auto-greet: assistant always talks first
    if (chatEl) {
        let currentScreen = 'landing';
        if (!document.getElementById('start-section')?.classList.contains('hidden')) currentScreen = 'setup';
        else if (!document.getElementById('capture-section')?.classList.contains('hidden')) currentScreen = 'capture';
        else if (!document.getElementById('review-section')?.classList.contains('hidden')) currentScreen = 'review';
        else if (!document.getElementById('analytics-section')?.classList.contains('hidden')) currentScreen = 'results';

        const greetings = {
            'landing': "Hey! 👋 I'm your grading assistant. Tell me what you need — set up a session, add students, check results — I've got you.",
            'setup': "Setting up? I can help pick the right assessment type, add a missing student, or get you scanning faster.",
            'capture': "Scanning scripts — nice! If a name isn't matching or someone's missing, just tell me.",
            'review': "Reviewing scores? I can correct names, fix scores, or add someone who's missing from the list.",
            'results': "Results are in! Want me to analyze scores, find at-risk students, or download your Excel?"
        };
        const greeting = greetings[currentScreen] || greetings['landing'];

        setTimeout(() => {
            chatEl.innerHTML = `
                <div class="flex justify-start mb-3 animate-fade-in-up">
                    <div class="bg-white/5 border border-white/10 rounded-2xl rounded-bl-md px-4 py-3 max-w-[85%]">
                        <p class="text-sm text-white leading-relaxed">${greeting}</p>
                    </div>
                </div>
            `;
            assistantHistory.push({ role: 'assistant', text: greeting });
        }, 400);
    }

    // Attach iOS keyboard focus fix
    const inputEl = document.getElementById('assistant-input');
    if (inputEl) {
        // Only attach once
        if (!inputEl.dataset.hasKeyboardFix) {
            inputEl.addEventListener('focus', () => {
                // On iOS, the visual viewport shrinks but the layout viewport doesn't always. 
                // Push the panel up explicitly.
                requestAnimationFrame(() => {
                    modal.style.paddingBottom = '60px'; // Approx height of iOS Safari accessory bar
                    setTimeout(() => chatEl.scrollTop = chatEl.scrollHeight, 100);
                });
            });
            inputEl.addEventListener('blur', () => {
                // Delay slightly to handle keyboard fully closing before reverting padding
                setTimeout(() => {
                    modal.style.paddingBottom = ''; // Clear inline style so css takes over
                }, 100);
            });
            inputEl.dataset.hasKeyboardFix = 'true';
        }
    }
}

async function sendAssistantMessage(message) {
    if (!message || !message.trim()) return;

    const chatEl = document.getElementById('assistant-chat');
    const inputEl = document.getElementById('assistant-input');
    if (!chatEl) return;

    // Add user message to history
    const displayMessage = message.startsWith('[SYSTEM]') ? null : message;
    assistantHistory.push({ role: 'user', text: message });

    // Add user message to chat (skip system messages)
    if (displayMessage) {
        chatEl.innerHTML += `
            <div class="flex justify-end mb-3">
                <div class="bg-primary/20 border border-primary/30 rounded-2xl rounded-br-md px-4 py-2 max-w-[80%]">
                    <p class="text-sm text-white">${displayMessage}</p>
                </div>
            </div>
        `;
    }

    // Clear input
    if (inputEl) inputEl.value = '';

    // Show typing indicator
    chatEl.innerHTML += `
        <div id="assistant-typing" class="flex justify-start mb-3">
            <div class="bg-white/5 border border-white/10 rounded-2xl rounded-bl-md px-4 py-2">
                <div class="flex gap-1.5">
                    <span class="w-2 h-2 bg-white/40 rounded-full animate-bounce" style="animation-delay: 0ms"></span>
                    <span class="w-2 h-2 bg-white/40 rounded-full animate-bounce" style="animation-delay: 150ms"></span>
                    <span class="w-2 h-2 bg-white/40 rounded-full animate-bounce" style="animation-delay: 300ms"></span>
                </div>
            </div>
        </div>
    `;
    chatEl.scrollTop = chatEl.scrollHeight;

    // Detect current screen
    let currentScreen = 'landing';
    if (!document.getElementById('start-section')?.classList.contains('hidden')) currentScreen = 'setup';
    else if (!document.getElementById('capture-section')?.classList.contains('hidden')) currentScreen = 'capture';
    else if (!document.getElementById('review-section')?.classList.contains('hidden')) currentScreen = 'review';
    else if (!document.getElementById('analytics-section')?.classList.contains('hidden')) currentScreen = 'results';

    try {
        // Build live session info for v3 assistant
        const sessionInfo = {
            classesGraded: [...sessionClasses],
            subject: document.getElementById('subject-name')?.value || '',
            assessmentsDone: [],
            selectedClasses: [...selectedClasses]
        };
        // Send current results if available
        const liveResults = (typeof extractedData !== 'undefined' && extractedData) ?
            extractedData.filter(r => r && r.name).map(r => ({
                name: r.name || '', score: r.score || '', class: r.class || ''
            })) : [];

        const response = await fetch('/api/smart-assistant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                context: assistantContext,
                currentScreen: currentScreen,
                currentResults: liveResults,
                sessionInfo: sessionInfo,
                history: assistantHistory.slice(-20) // Send last 20 messages for context
            })
        });
        const data = await response.json();

        // Track assistant response in history
        assistantHistory.push({ role: 'assistant', text: data.response || '', action: data.action || 'none' });

        // Remove typing indicator
        document.getElementById('assistant-typing')?.remove();

        // Add assistant response
        let actionBtn = '';
        // Auto-execute direct actions immediately instead of showing a button
        const autoExecActions = [
            'setup_session', 'correct_score', 'add_student', 'add_students_batch',
            'move_student', 'export_data', 'scan_papers', 'edit_scores',
            'view_standings', 'add_class', 'update_roster', 'manage_enrollment',
            'edit_excel'
        ];
        if (data.action && data.action !== 'none') {
            if (autoExecActions.includes(data.action)) {
                // Auto-execute after a small delay so the response text renders first
                setTimeout(() => {
                    executeAssistantAction(data.action, data.params || {});
                }, 600);
            } else {
                // Show a button for analysis/insight actions
                const actionLabels = {
                    'analyze_scores': '📊 Show analysis',
                    'compare_classes': '⚖️ Compare',
                    'compare_assessments': '📊 Compare assessments',
                    'flag_anomalies': '🔍 Show issues',
                    'find_at_risk': '⚠️ Show at-risk',
                    'generate_report': '📋 Generate',
                };
                const label = actionLabels[data.action] || '▶ Do this';
                actionBtn = `
                    <button onclick="executeAssistantAction('${data.action}', ${JSON.stringify(data.params || {}).replace(/"/g, '&quot;')})"
                        class="mt-2 px-3 py-1.5 bg-primary/20 hover:bg-primary/30 text-primary text-xs font-bold rounded-lg transition-all border border-primary/30 inline-flex items-center gap-1.5">
                        ${label}
                    </button>
                `;
            }
        }

        // Render rich insight cards if the action returns them
        let insightsHTML = '';
        if (data.params?.insights && Array.isArray(data.params.insights)) {
            insightsHTML = `<div class="mt-3 space-y-1.5">${data.params.insights.map(i =>
                `<div class="flex items-start gap-2 text-xs text-white/70"><i class="fa-solid fa-chart-line text-primary mt-0.5"></i><span>${i}</span></div>`
            ).join('')}</div>`;
        }
        if (data.params?.anomalies && Array.isArray(data.params.anomalies)) {
            insightsHTML += `<div class="mt-3 space-y-1.5">${data.params.anomalies.map(a =>
                `<div class="flex items-start gap-2 text-xs text-amber-400"><i class="fa-solid fa-triangle-exclamation mt-0.5"></i><span><strong>${a.name}</strong>: ${a.score} — ${a.reason || 'unusual score'}</span></div>`
            ).join('')}</div>`;
        }
        if (data.params?.at_risk && Array.isArray(data.params.at_risk)) {
            insightsHTML += `<div class="mt-3 space-y-1.5">${data.params.at_risk.map(s =>
                `<div class="flex items-start gap-2 text-xs text-red-400"><i class="fa-solid fa-circle-exclamation mt-0.5"></i><span><strong>${s.name}</strong> (${s.class || ''}) — Score: ${s.score}</span></div>`
            ).join('')}</div>`;
        }
        if (data.params?.report_text) {
            insightsHTML += `<div class="mt-3 p-3 bg-white/5 rounded-lg border border-white/10 text-xs text-white/80 whitespace-pre-line">${data.params.report_text}</div>`;
        }

        chatEl.innerHTML += `
            <div class="flex justify-start mb-3">
                <div class="bg-white/5 border border-white/10 rounded-2xl rounded-bl-md px-4 py-3 max-w-[85%]">
                    <p class="text-sm text-white leading-relaxed whitespace-pre-wrap">${data.response || 'I\'m not sure how to help with that.'}</p>
                    ${insightsHTML}
                    ${actionBtn}
                </div>
            </div>
        `;
        chatEl.scrollTop = chatEl.scrollHeight;

    } catch (e) {
        document.getElementById('assistant-typing')?.remove();
        chatEl.innerHTML += `
            <div class="flex justify-start mb-3">
                <div class="bg-destructive/10 border border-destructive/20 rounded-2xl rounded-bl-md px-4 py-2">
                    <p class="text-sm text-destructive">Sorry, something went wrong. Try again?</p>
                </div>
            </div>
        `;
    }
}

async function executeAssistantAction(action, params) {
    const modal = document.getElementById('smart-assistant-modal');
    const fab = document.getElementById('smart-assistant-fab');
    const hideModal = () => {
        if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
        if (fab) fab.classList.remove('hidden');
    };

    switch (action) {
        case 'setup_session': {
            // Smart setup: pre-fill class, subject, assessment and navigate
            hideModal();

            // Show setup section
            if (typeof window.showSetup === 'function') {
                window.showSetup(null);
            }

            // Pre-fill class
            if (params?.class_name) {
                const tc = document.getElementById('target-class');
                if (tc) {
                    let found = false;
                    for (let opt of tc.options) {
                        if (opt.value.toLowerCase() === params.class_name.toLowerCase()) {
                            tc.value = opt.value; found = true; break;
                        }
                    }
                    if (!found) tc.add(new Option(params.class_name, params.class_name, true, true));
                    tc.dispatchEvent(new Event('change'));
                }
                // Also select in chip picker
                if (!selectedClasses.includes(params.class_name)) {
                    selectedClasses.push(params.class_name);
                    addToSessionClasses(params.class_name);
                    initClassPicker();
                }
            }
            // Pre-fill subject
            if (params?.subject_name) {
                const sj = document.getElementById('subject-name');
                const csj = document.getElementById('custom-subject');
                const csjw = document.getElementById('custom-subject-wrapper');
                if (sj) {
                    sj.value = 'custom';
                    sj.dispatchEvent(new Event('change'));
                    if (csjw) csjw.classList.remove('hidden');
                    if (csj) csj.value = params.subject_name;
                }
            }
            // Pre-fill assessment type
            if (params?.assessment_type) {
                const at = document.getElementById('assessment-type');
                if (at) {
                    let found = false;
                    for (let opt of at.options) {
                        if (opt.value.toLowerCase() === params.assessment_type.toLowerCase()) {
                            at.value = opt.value; found = true; break;
                        }
                    }
                    if (!found) {
                        at.value = 'custom';
                        at.dispatchEvent(new Event('change'));
                        const ci = document.getElementById('custom-assessment');
                        if (ci) ci.value = params.assessment_type;
                    }
                }
            }
            if (typeof showToast === 'function') {
                showToast('Session set up! Ready to scan.', 'success');
            }
            break;
        }
        case 'scan_papers': {
            // Navigate directly to setup phase so user can pick class + subject + start camera
            hideModal();

            // Show setup phase, hide others
            const landing = document.getElementById('landing-phase');
            const setup = document.getElementById('setup-phase');
            const captureEl = document.getElementById('capture-section');
            const reviewEl = document.getElementById('review-section');
            const analyticsEl = document.getElementById('analytics-section');
            const heroEl = document.getElementById('app-hero');

            if (landing) landing.classList.add('hidden');
            if (setup) setup.classList.remove('hidden');
            if (captureEl) captureEl.classList.add('hidden');
            if (reviewEl) reviewEl.classList.add('hidden');
            if (analyticsEl) analyticsEl.classList.add('hidden');

            // Init class picker if available
            if (typeof initClassPicker === 'function') initClassPicker();

            if (typeof showToast === 'function') {
                showToast('Pick your class, subject, and assessment — then start scanning!', 'info', 4000);
            }
            break;
        }
        case 'edit_scores': {
            // Navigate directly to review section if it has data, otherwise prompt
            hideModal();

            const reviewScores = document.getElementById('review-section');
            const captureScore = document.getElementById('capture-section');
            const analyticsScore = document.getElementById('analytics-section');
            const startScore = document.getElementById('start-section');

            if (typeof extractedData !== 'undefined' && extractedData && extractedData.length > 0) {
                // We have scanned data — show the review section
                if (reviewScores) reviewScores.classList.remove('hidden');
                if (captureScore) captureScore.classList.add('hidden');
                if (analyticsScore) analyticsScore.classList.add('hidden');
                if (startScore) startScore.classList.add('hidden');
                const heroFix = document.getElementById('app-hero');
                if (heroFix) heroFix.classList.add('hidden');
                if (typeof showToast === 'function') {
                    showToast('Tap any name or score to edit it.', 'info', 3000);
                }
            } else {
                if (typeof showToast === 'function') {
                    showToast('No scores to fix yet — scan some scripts first!', 'warning', 4000);
                }
            }
            break;
        }
        case 'view_standings': {
            hideModal();

            const analyticsView = document.getElementById('analytics-section');
            const reviewView = document.getElementById('review-section');
            const captureView = document.getElementById('capture-section');
            const startView = document.getElementById('start-section');

            if (analyticsView && !analyticsView.innerHTML.trim().includes('No data')) {
                // Show analytics/results section
                if (analyticsView) analyticsView.classList.remove('hidden');
                if (reviewView) reviewView.classList.add('hidden');
                if (captureView) captureView.classList.add('hidden');
                if (startView) startView.classList.add('hidden');
                const heroView = document.getElementById('app-hero');
                if (heroView) heroView.classList.add('hidden');
            } else {
                // No results yet — try clicking export if we have data
                if (typeof extractedData !== 'undefined' && extractedData && extractedData.length > 0) {
                    document.getElementById('btn-export')?.click();
                } else {
                    if (typeof showToast === 'function') {
                        showToast('No results yet — grade some scripts first!', 'warning', 4000);
                    }
                }
            }
            break;
        }
        case 'add_student': {
            if (params?.student_name && params?.class_name) {
                safeAddStudent(params.student_name, params.class_name);
            } else {
                // Open inline roster editor directly instead of sending a chat message
                executeAssistantAction('update_roster', params);
            }
            break;
        }
        case 'add_students_batch': {
            if (params?.students && params?.class_name) {
                params.students.forEach(name => safeAddStudent(name, params.class_name));
            }
            break;
        }
        case 'correct_score': {
            if (params?.student_name && params?.new_score !== undefined) {
                // Update in-memory extractedData + DOM (instant visual feedback)
                let found = false;
                if (typeof extractedData !== 'undefined') {
                    const targetName = params.student_name.toLowerCase();
                    for (let i = 0; i < extractedData.length; i++) {
                        if ((extractedData[i].name || '').toLowerCase().includes(targetName)) {
                            extractedData[i].score = params.new_score;
                            const scoreInput = document.querySelector(`input[data-index="${i}"][data-field="score"]`);
                            if (scoreInput) {
                                scoreInput.value = params.new_score;
                                scoreInput.classList.add('ring-2', 'ring-emerald-500', 'bg-emerald-500/20');
                                setTimeout(() => scoreInput.classList.remove('ring-2', 'ring-emerald-500', 'bg-emerald-500/20'), 2000);
                            }
                            found = true;
                            break;
                        }
                    }
                }

                // Show result in chat with re-export button
                const chatEl = document.getElementById('assistant-chat');
                if (chatEl) {
                    if (found) {
                        chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-emerald-500/10 border border-emerald-500/20 rounded-2xl rounded-bl-md px-4 py-3 max-w-[90%]">
                            <p class="text-sm text-emerald-400 font-bold mb-2"><i class="fa-solid fa-check-circle mr-1.5"></i> ${params.student_name}'s score updated to ${params.new_score}</p>
                            <p class="text-[11px] text-white/40 mb-2">Changed in the current session. Hit save to keep it.</p>
                            <button onclick="document.getElementById('btn-export')?.click()" class="inline-flex items-center gap-2 px-4 py-2 bg-primary/20 hover:bg-primary/30 border border-primary/30 rounded-xl text-primary text-xs font-bold transition-all"><i class="fa-solid fa-file-arrow-down"></i> Save & Download Now</button>
                        </div></div>`;
                    } else {
                        chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-amber-500/10 border border-amber-500/20 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-amber-400 font-bold"><i class="fa-solid fa-triangle-exclamation mr-1.5"></i> Couldn't find "${params.student_name}" in the current grades. Make sure you've scanned their paper first.</p></div></div>`;
                    }
                    chatEl.scrollTop = chatEl.scrollHeight;
                }
            }
            break;
        }
        case 'move_student': {
            if (params?.student_name && params?.class_name && params?.target_class) {
                // Move via API
                fetch('/api/move-student', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ studentName: params.student_name, fromClass: params.class_name, toClass: params.target_class })
                }).then(r => r.json()).then(data => {
                    const chatEl = document.getElementById('assistant-chat');
                    if (chatEl) {
                        chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-emerald-500/10 border border-emerald-500/20 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-emerald-400">${data.message || data.error || 'Done!'}</p></div></div>`;
                        chatEl.scrollTop = chatEl.scrollHeight;
                    }
                });
            }
            break;
        }
        case 'analyze_scores':
        case 'compare_classes':
        case 'compare_assessments':
        case 'flag_anomalies':
        case 'find_at_risk':
        case 'generate_report':
            // These actions return data in params — already rendered via insightsHTML above
            break;
        case 'edit_excel': {
            // Teacher asked to edit the uploaded Excel — send file + instruction to backend
            if (typeof _assistantUploadedFile !== 'undefined' && _assistantUploadedFile) {
                const chatEl = document.getElementById('assistant-chat');
                if (chatEl) {
                    chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-white/5 border border-white/10 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-white/60"><i class="fa-solid fa-circle-notch fa-spin mr-2"></i>Editing your Excel...</p></div></div>`;
                    chatEl.scrollTop = chatEl.scrollHeight;
                }
                const editFormData = new FormData();
                editFormData.append('file', _assistantUploadedFile);
                editFormData.append('instruction', params?.instruction || message || '');
                try {
                    const editResp = await fetch('/api/assistant-edit-excel', {
                        method: 'POST',
                        body: editFormData
                    });
                    const editData = await editResp.json();
                    // Remove editing indicator
                    chatEl?.querySelector('.fa-circle-notch')?.closest('.flex')?.remove();

                    if (editData.needs_confirmation) {
                        if (chatEl) {
                            const uniqueId = Date.now();
                            chatEl.innerHTML += `<div class="flex justify-start mb-3" id="confirm-${uniqueId}"><div class="bg-indigo-500/10 border border-indigo-500/30 rounded-2xl rounded-bl-md p-4 max-w-[90%]">
                                <p class="text-sm text-white font-bold mb-3"><i class="fa-solid fa-circle-question text-indigo-400 mr-2"></i>${editData.message}</p>
                                <div class="flex gap-2">
                                    <button onclick="confirmColumnEdit(${uniqueId}, '${editData.original_instruction.replace(/'/g, "\\'")}', '${editData.guess.replace(/'/g, "\\'")}', true)" class="flex-1 py-2 bg-indigo-500 hover:bg-indigo-600 rounded-xl text-white text-xs font-bold transition-all">Yes, use that</button>
                                    <button onclick="confirmColumnEdit(${uniqueId}, '${editData.original_instruction.replace(/'/g, "\\'")}', '${editData.guess.replace(/'/g, "\\'")}', false)" class="flex-1 py-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-xl text-white text-xs transition-all">No, create new</button>
                                </div>
                            </div></div>`;
                            chatEl.scrollTop = chatEl.scrollHeight;
                        }
                    } else if (editData.success) {
                        if (chatEl) {
                            chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-emerald-500/10 border border-emerald-500/20 rounded-2xl rounded-bl-md px-4 py-3 max-w-[90%]">
                                <p class="text-sm text-emerald-400 font-bold mb-1"><i class="fa-solid fa-check-circle mr-1.5"></i>${editData.message || editData.summary}</p>
                                <a href="${editData.download_url}" class="inline-flex mt-2 items-center gap-2 px-4 py-2 bg-primary/20 hover:bg-primary/30 border border-primary/30 rounded-xl text-primary text-xs font-bold transition-all"><i class="fa-solid fa-file-arrow-down"></i> Download Edited File</a>
                            </div></div>`;
                            chatEl.scrollTop = chatEl.scrollHeight;
                        }
                    } else {
                        if (chatEl) {
                            chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-amber-500/10 border border-amber-500/20 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-amber-400">${editData.error || 'Could not apply edits.'}</p></div></div>`;
                            chatEl.scrollTop = chatEl.scrollHeight;
                        }
                    }
                } catch (editErr) {
                    chatEl?.querySelector('.fa-circle-notch')?.closest('.flex')?.remove();
                    if (chatEl) {
                        chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-destructive/10 border border-destructive/20 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-destructive">Edit failed: ${editErr.message}</p></div></div>`;
                        chatEl.scrollTop = chatEl.scrollHeight;
                    }
                }
            } else {
                const chatEl = document.getElementById('assistant-chat');
                if (chatEl) {
                    chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-amber-500/10 border border-amber-500/20 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-amber-400 font-bold"><i class="fa-solid fa-paperclip mr-1.5"></i>Upload an Excel file first using the 📎 button, then tell me what to change.</p></div></div>`;
                    chatEl.scrollTop = chatEl.scrollHeight;
                }
            }
            break;
        }
        case 'scan_image_to_excel': {
            if (typeof _assistantUploadedFiles !== 'undefined' && _assistantUploadedFiles.length > 0) {
                const chatEl = document.getElementById('assistant-chat');
                if (chatEl) {
                    chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-blue-500/10 border border-blue-500/20 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-blue-400"><i class="fa-solid fa-circle-notch fa-spin mr-2"></i>Scanning complex table into Excel...</p></div></div>`;
                    chatEl.scrollTop = chatEl.scrollHeight;
                }
                const scanFormData = new FormData();
                _assistantUploadedFiles.forEach(f => scanFormData.append('images', f));
                scanFormData.append('instruction', params?.instruction || message || '');
                scanFormData.append('class_name', params?.class_name || '');
                try {
                    const scanResp = await fetch('/api/assistant-scan-to-excel', {
                        method: 'POST',
                        body: scanFormData
                    });
                    const scanData = await scanResp.json();
                    chatEl?.querySelector('.fa-circle-notch')?.closest('.flex')?.remove();

                    if (scanData.success) {
                        if (chatEl) {
                            chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-emerald-500/10 border border-emerald-500/20 rounded-2xl rounded-bl-md px-4 py-3 max-w-[90%]">
                                <p class="text-sm text-emerald-400 font-bold mb-1"><i class="fa-solid fa-check-circle mr-1.5"></i>${scanData.message}</p>
                                <a href="${scanData.download_url}" class="inline-flex mt-2 items-center gap-2 px-4 py-2 bg-primary/20 hover:bg-primary/30 border border-primary/30 rounded-xl text-primary text-xs font-bold transition-all"><i class="fa-solid fa-file-arrow-down"></i> Download Excel File</a>
                            </div></div>`;
                            chatEl.scrollTop = chatEl.scrollHeight;
                        }
                    } else {
                        if (chatEl) {
                            chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-amber-500/10 border border-amber-500/20 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-amber-400">${scanData.error || 'Could not scan table.'}</p></div></div>`;
                            chatEl.scrollTop = chatEl.scrollHeight;
                        }
                    }
                } catch (scanErr) {
                    chatEl?.querySelector('.fa-circle-notch')?.closest('.flex')?.remove();
                    if (chatEl) {
                        chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-destructive/10 border border-destructive/20 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-destructive">Scan failed: ${scanErr.message}</p></div></div>`;
                        chatEl.scrollTop = chatEl.scrollHeight;
                    }
                }
            } else {
                const chatEl = document.getElementById('assistant-chat');
                if (chatEl) {
                    chatEl.innerHTML += `<div class="flex justify-start mb-3"><div class="bg-amber-500/10 border border-amber-500/20 rounded-2xl rounded-bl-md px-4 py-2"><p class="text-sm text-amber-400 font-bold"><i class="fa-solid fa-image mr-1.5"></i>Please upload an image of the table first using the 📎 button.</p></div></div>`;
                    chatEl.scrollTop = chatEl.scrollHeight;
                }
            }
            break;
        }
        case 'add_class':
            hideModal();
            document.getElementById('btn-open-paste-modal')?.click();
            break;
        case 'manage_enrollment':
        case 'update_roster':
            // Build inline roster editor in assistant chat
            (async () => {
                const chatEl = document.getElementById('assistant-chat');
                if (!chatEl) return;

                // Show loading
                chatEl.innerHTML += `
                    <div class="flex justify-start mb-3" id="roster-editor-area">
                        <div class="bg-white/5 border border-white/10 rounded-2xl rounded-bl-md px-4 py-3 w-full max-w-[95%]">
                            <p class="text-sm text-white/60"><i class="fa-solid fa-circle-notch fa-spin mr-2"></i>Loading classes...</p>
                        </div>
                    </div>
                `;
                chatEl.scrollTop = chatEl.scrollHeight;

                try {
                    const res = await fetch('/api/classes');
                    const classes = await res.json();

                    if (!classes.length) {
                        document.getElementById('roster-editor-area').innerHTML = `
                            <div class="bg-white/5 border border-white/10 rounded-2xl rounded-bl-md px-4 py-3 w-full">
                                <p class="text-sm text-white/70">No classes found yet. Create a class first!</p>
                            </div>
                        `;
                        return;
                    }

                    const classOptions = classes.map(c =>
                        `<option value="${c.id}" data-name="${c.name}">${c.name} (${c.student_count || 0})</option>`
                    ).join('');

                    document.getElementById('roster-editor-area').innerHTML = `
                        <div class="bg-white/5 border border-violet-500/20 rounded-2xl rounded-bl-md px-4 py-4 w-full">
                            <h4 class="text-sm font-bold text-violet-300 mb-3 flex items-center gap-2">
                                <i class="fa-solid fa-list-check"></i> Class Roster Editor
                            </h4>
                            <select id="roster-class-select" onchange="window._loadRosterStudents(this.value)"
                                class="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white mb-3 outline-none focus:border-violet-500/40">
                                <option value="">Pick a class...</option>
                                ${classOptions}
                            </select>
                            <div id="roster-student-list" class="space-y-1"></div>
                            <div id="roster-add-area" class="hidden mt-3 flex gap-2">
                                <input id="roster-new-name" type="text" placeholder="New student name..."
                                    class="flex-1 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white outline-none focus:border-emerald-500/40"
                                    onkeydown="if(event.key==='Enter')window._addRosterStudent()">
                                <button onclick="window._addRosterStudent()"
                                    class="px-3 py-2 bg-emerald-500/20 text-emerald-400 text-xs font-bold rounded-lg border border-emerald-500/20 hover:bg-emerald-500/30 transition-colors">
                                    <i class="fa-solid fa-plus mr-1"></i>Add
                                </button>
                            </div>
                        </div>
                    `;

                    // Load students for a class
                    window._loadRosterStudents = async function (classId) {
                        const listEl = document.getElementById('roster-student-list');
                        const addArea = document.getElementById('roster-add-area');
                        if (!classId) { listEl.innerHTML = ''; addArea?.classList.add('hidden'); return; }

                        listEl.innerHTML = '<p class="text-xs text-white/40"><i class="fa-solid fa-circle-notch fa-spin mr-1"></i>Loading...</p>';
                        addArea?.classList.remove('hidden');

                        const sRes = await fetch('/api/students?class_id=' + classId);
                        const students = await sRes.json();

                        if (!students.length) {
                            listEl.innerHTML = '<p class="text-xs text-white/40 italic py-2">No students yet. Add some below!</p>';
                            return;
                        }

                        listEl.innerHTML = `
                            <p class="text-[10px] text-white/30 uppercase tracking-wider mb-1 font-bold">${students.length} students</p>
                            ${students.map((s, i) => `
                                <div class="flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-white/5 group transition-colors" id="roster-row-${s.id}">
                                    <span class="text-sm text-white/80 flex items-center gap-2">
                                        <span class="text-[10px] text-white/20 w-5">${i + 1}.</span>
                                        <span id="roster-name-${s.id}">${s.name}</span>
                                    </span>
                                    <div class="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                        <button onclick="window._editRosterStudent(${s.id}, '${s.name.replace(/'/g, "\\'")}')" 
                                            class="w-6 h-6 flex items-center justify-center rounded text-white/30 hover:text-blue-400 hover:bg-blue-500/10 transition-colors" title="Rename">
                                            <i class="fa-solid fa-pen text-[10px]"></i>
                                        </button>
                                        <button onclick="window._removeRosterStudent(${s.id}, '${s.name.replace(/'/g, "\\'")}')" 
                                            class="w-6 h-6 flex items-center justify-center rounded text-white/30 hover:text-red-400 hover:bg-red-500/10 transition-colors" title="Remove">
                                            <i class="fa-solid fa-xmark text-[10px]"></i>
                                        </button>
                                    </div>
                                </div>
                            `).join('')}
                        `;
                        chatEl.scrollTop = chatEl.scrollHeight;
                    };

                    // Add student
                    window._addRosterStudent = async function () {
                        const input = document.getElementById('roster-new-name');
                        const classId = document.getElementById('roster-class-select').value;
                        const name = input?.value?.trim();
                        if (!name || !classId) return;
                        input.value = '';

                        const res = await fetch('/api/students', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name, class_id: parseInt(classId) })
                        });
                        const data = await res.json();
                        if (res.ok) {
                            if (typeof showToast === 'function') showToast('Added ' + name, 'success');
                            window._loadRosterStudents(classId);
                        } else {
                            if (typeof showToast === 'function') showToast(data.error || 'Failed to add', 'error');
                        }
                    };

                    // Remove student
                    window._removeRosterStudent = async function (id, name) {
                        const row = document.getElementById('roster-row-' + id);
                        if (row) row.style.opacity = '0.3';

                        const res = await fetch('/api/students/' + id, { method: 'DELETE' });
                        if (res.ok) {
                            if (row) row.remove();
                            if (typeof showToast === 'function') showToast('Removed ' + name, 'info');
                            // Update count
                            const classId = document.getElementById('roster-class-select').value;
                            if (classId) window._loadRosterStudents(classId);
                        } else {
                            if (row) row.style.opacity = '1';
                            if (typeof showToast === 'function') showToast('Failed to remove', 'error');
                        }
                    };

                    // Edit/rename student
                    window._editRosterStudent = function (id, currentName) {
                        const nameEl = document.getElementById('roster-name-' + id);
                        if (!nameEl) return;
                        nameEl.innerHTML = `
                            <input type="text" value="${currentName}" 
                                class="bg-white/10 border border-blue-500/30 rounded px-2 py-0.5 text-sm text-white outline-none w-36"
                                onkeydown="if(event.key==='Enter')window._saveRosterEdit(${id}, this.value);if(event.key==='Escape'){this.parentElement.textContent='${currentName.replace(/'/g, "\\'")}'}"
                                onblur="window._saveRosterEdit(${id}, this.value)" autofocus>
                        `;
                        nameEl.querySelector('input').focus();
                    };

                    window._saveRosterEdit = async function (id, newName) {
                        newName = newName?.trim();
                        if (!newName) return;
                        const res = await fetch('/api/students/' + id, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ name: newName })
                        });
                        if (res.ok) {
                            if (typeof showToast === 'function') showToast('Renamed to ' + newName, 'success');
                            const classId = document.getElementById('roster-class-select').value;
                            if (classId) window._loadRosterStudents(classId);
                        }
                    };

                } catch (err) {
                    document.getElementById('roster-editor-area').innerHTML = `
                        <div class="bg-white/5 border border-red-500/20 rounded-2xl rounded-bl-md px-4 py-3 w-full">
                            <p class="text-sm text-red-400">Failed to load classes. Please try again.</p>
                        </div>
                    `;
                }
            })();
            break;
        case 'export_data':
            hideModal();
            document.getElementById('btn-export')?.click();
            break;
        default:
            if (typeof showToast === 'function') showToast('Action: ' + action, 'info');
    }
}

// === SAFE ADD STUDENT ===
async function safeAddStudent(studentName, className) {
    const chatEl = document.getElementById('assistant-chat');
    if (!chatEl) return;

    try {
        const response = await fetch('/api/safe-add-student', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ studentName, className })
        });
        const data = await response.json();

        if (data.success) {
            chatEl.innerHTML += `
                <div class="flex justify-start mb-3">
                    <div class="bg-emerald-500/10 border border-emerald-500/20 rounded-2xl rounded-bl-md px-4 py-3 max-w-[80%]">
                        <p class="text-sm text-emerald-400 font-bold"><i class="fa-solid fa-check-circle mr-1.5"></i> ${data.message}</p>
                        <p class="text-xs text-emerald-300/60 mt-1">${className} now has ${data.total_students} students.</p>
                    </div>
                </div>
            `;
        } else if (data.needs_confirmation) {
            chatEl.innerHTML += `
                <div class="flex justify-start mb-3">
                    <div class="bg-amber-500/10 border border-amber-500/20 rounded-2xl rounded-bl-md px-4 py-3 max-w-[85%]">
                        <p class="text-sm text-amber-400 font-bold"><i class="fa-solid fa-triangle-exclamation mr-1.5"></i> ${data.warning}</p>
                        <div class="flex gap-2 mt-3">
                            <button onclick="safeAddStudentForce('${studentName.replace(/'/g, "\\'")}', '${className.replace(/'/g, "\\'")}')" 
                                class="px-3 py-1.5 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 text-xs font-bold rounded-lg border border-emerald-500/30">
                                Yes, add as new student
                            </button>
                            <button onclick="this.closest('.flex.justify-start').remove()"
                                class="px-3 py-1.5 bg-white/5 hover:bg-white/10 text-white/60 text-xs font-bold rounded-lg border border-white/10">
                                Cancel - same person
                            </button>
                        </div>
                    </div>
                </div>
            `;
        } else if (data.duplicate) {
            chatEl.innerHTML += `
                <div class="flex justify-start mb-3">
                    <div class="bg-white/5 border border-white/10 rounded-2xl rounded-bl-md px-4 py-2">
                        <p class="text-sm text-white"><i class="fa-solid fa-info-circle mr-1.5 text-blue-400"></i> ${data.error}</p>
                    </div>
                </div>
            `;
        } else {
            chatEl.innerHTML += `
                <div class="flex justify-start mb-3">
                    <div class="bg-destructive/10 border border-destructive/20 rounded-2xl rounded-bl-md px-4 py-2">
                        <p class="text-sm text-destructive">${data.error || 'Something went wrong.'}</p>
                    </div>
                </div>
            `;
        }
        chatEl.scrollTop = chatEl.scrollHeight;
    } catch (e) {
        console.error('Safe add student error:', e);
    }
}

async function safeAddStudentForce(studentName, className) {
    try {
        const response = await fetch('/api/safe-add-student', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ studentName, className, force: true })
        });
        const data = await response.json();
        const chatEl = document.getElementById('assistant-chat');
        if (chatEl && data.success) {
            chatEl.innerHTML += `
                <div class="flex justify-start mb-3">
                    <div class="bg-emerald-500/10 border border-emerald-500/20 rounded-2xl rounded-bl-md px-4 py-3">
                        <p class="text-sm text-emerald-400 font-bold"><i class="fa-solid fa-check-circle mr-1.5"></i> ${data.message}</p>
                    </div>
                </div>
            `;
            chatEl.scrollTop = chatEl.scrollHeight;
        }
    } catch (e) {
        console.error('Force add error:', e);
    }
}
// === FILE UPLOAD IN ASSISTANT CHAT ===
let _assistantUploadedFile = null; // Store reference to last uploaded file for follow-up edits
let _assistantUploadedFiles = []; // Store multiple images

async function handleAssistantFileUpload(input) {
    if (!input.files || input.files.length === 0) return;
    const files = Array.from(input.files);
    input.value = ''; // Reset so same file can be re-uploaded

    const chatEl = document.getElementById('assistant-chat');
    if (!chatEl) return;

    const isImageBatch = files.every(f => f.type.startsWith('image/'));
    const isExcel = /\.(xlsx|xls|csv)$/i.test(files[0].name);

    if (isImageBatch) {
        _assistantUploadedFiles = files; // Store all images

        let fileDisplayHTML = '';
        files.forEach(f => {
            fileDisplayHTML += `
                <div class="bg-primary/10 border border-primary/20 rounded-2xl rounded-br-md px-4 py-2 mt-2 w-fit ml-auto">
                    <p class="text-sm text-white"><i class="fa-solid fa-image text-blue-400 mr-2"></i>${f.name}</p>
                    <p class="text-[10px] text-white/40 mt-1">${(f.size / 1024).toFixed(1)} KB</p>
                </div>
            `;
        });

        chatEl.innerHTML += `
            <div class="flex justify-end mb-3 flex-col items-end">
                ${fileDisplayHTML}
            </div>
            <div class="flex justify-start mb-3">
                <div class="bg-blue-500/10 border border-blue-500/20 rounded-2xl rounded-bl-md px-4 py-3 max-w-[85%]">
                    <p class="text-sm text-blue-400 font-bold mb-1"><i class="fa-solid fa-images mr-1.5"></i>${files.length} images attached!</p>
                    <p class="text-[11px] text-white/50">I'm ready to analyze these. Over to you.</p>
                </div>
            </div>
        `;
        chatEl.scrollTop = chatEl.scrollHeight;

        sendAssistantMessage(`[SYSTEM] Teacher just attached ${files.length} image(s) of a document or table. Acknowledge this, and ask them two things: 1) What specific columns or data they want to extract into an Excel file, and 2) WHICH CLASS this is for, so you can perfectly match the messy handwriting against the official database roster.`);

    } else if (isExcel) {
        const file = files[0]; // Only process the first excel

        // Show upload indicator
        chatEl.innerHTML += `
            <div class="flex justify-end mb-3">
                <div class="bg-primary/10 border border-primary/20 rounded-2xl rounded-br-md px-4 py-2 max-w-[80%]">
                    <p class="text-sm text-white"><i class="fa-solid fa-file-excel text-emerald-400 mr-2"></i>${file.name}</p>
                    <p class="text-[10px] text-white/40 mt-1">${(file.size / 1024).toFixed(1)} KB</p>
                </div>
            </div>
        `;
        chatEl.innerHTML += `
            <div id="upload-typing" class="flex justify-start mb-3">
                <div class="bg-white/5 border border-white/10 rounded-2xl rounded-bl-md px-4 py-2">
                    <p class="text-sm text-white/60"><i class="fa-solid fa-circle-notch fa-spin mr-2"></i>Processing your file...</p>
                </div>
            </div>
        `;
        chatEl.scrollTop = chatEl.scrollHeight;

        try {
            const formData = new FormData();
            // Send Excel to upload endpoint
            formData.append('file', file);
            const response = await fetch('/api/upload-excel-scorelist', {
                method: 'POST',
                body: formData
            });
            const data = await response.json();

            // Remove typing indicator
            document.getElementById('upload-typing')?.remove();

            if (response.ok) {
                let summary = `Found ${data.total_students || '?'} students`;
                if (data.detected_class) summary += ` in ${data.detected_class} `;
                if (data.detected_subject) summary += ` — ${data.detected_subject} `;
                if (data.detected_assessments?.length) summary += `.Assessments: ${data.detected_assessments.join(', ')} `;

                chatEl.innerHTML += `
                    <div class="flex justify-start mb-3">
                        <div class="bg-emerald-500/10 border border-emerald-500/20 rounded-2xl rounded-bl-md px-4 py-3 max-w-[85%]">
                            <p class="text-sm text-emerald-400 font-bold"><i class="fa-solid fa-check-circle mr-1.5"></i>Excel loaded!</p>
                            <p class="text-xs text-white/70 mt-1">${summary}</p>
                        </div>
                    </div>
                `;
                // Now ask assistant to analyze it
                _assistantUploadedFile = file; // Store for follow-up edit instructions

                let fileNamesList = "";
                if (data.records && data.records.length > 0) {
                    const names = data.records.map(r => r.name);
                    fileNamesList = ` They are: ${names.join(', ')}.`;
                }

                sendAssistantMessage(`[SYSTEM] Teacher just uploaded ${file.name}. ${summary}.${fileNamesList} They might want to edit it — ask what they'd like to do with it.`);
            } else {
                chatEl.innerHTML += `
                    <div class="flex justify-start mb-3">
                        <div class="bg-destructive/10 border border-destructive/20 rounded-2xl rounded-bl-md px-4 py-2">
                            <p class="text-sm text-destructive">${data.error || "Couldn't parse the Excel file."}</p>
                        </div>
                    </div>
                `;
            }
        }
    } catch (err) {
        document.getElementById('upload-typing')?.remove();
        chatEl.innerHTML += `
            <div class="flex justify-start mb-3">
                <div class="bg-destructive/10 border border-destructive/20 rounded-2xl rounded-bl-md px-4 py-2">
                    <p class="text-sm text-destructive">Upload failed: ${err.message}</p>
                </div>
            </div>
        `;
    }
    chatEl.scrollTop = chatEl.scrollHeight;
}

function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}

// === PROACTIVE ASSISTANT TRIGGER ===
function triggerProactiveAssistant(eventType, details) {
    // Auto-open and send a system message when key events happen
    const msg = `[SYSTEM] ${eventType}: ${details}. Proactively suggest what to do next.Be warm, brief, and helpful.`;

    // Open assistant if not already open
    const modal = document.getElementById('smart-assistant-modal');
    if (modal && modal.classList.contains('hidden')) {
        if (typeof openSmartAssistant === 'function') openSmartAssistant();
    }

    setTimeout(() => sendAssistantMessage(msg), 500);
}

function closeSmartAssistant() {
    const modal = document.getElementById('smart-assistant-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
    // Show the FAB again
    const fab = document.getElementById('smart-assistant-fab');
    if (fab) fab.classList.remove('hidden');
}

// ═══════════════════════════════════════════════
//  INITIALIZATION — Hook into existing app
// ═══════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    // Initialize the class chip picker on page load so existing classes show up
    initClassPicker();

    // Show landing guide on first visit
    setTimeout(() => showGuide('landing'), 1500);

    // Track class selection from the old dropdown too
    const targetClass = document.getElementById('target-class');
    if (targetClass) {
        targetClass.addEventListener('change', () => {
            addToSessionClasses(targetClass.value);
        });
    }

    // Track subject selection
    const subjectSelect = document.getElementById('subject-name');
    if (subjectSelect) {
        subjectSelect.addEventListener('change', () => {
            if (selectedClasses.length > 0) {
                setTimeout(() => showGuide('assessment'), 500);
            }
        });
    }

    // Assistant chat input handler
    const assistantInput = document.getElementById('assistant-input');
    if (assistantInput) {
        assistantInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendAssistantMessage(assistantInput.value.trim());
            }
        });
    }

    const assistantSend = document.getElementById('assistant-send-btn');
    if (assistantSend) {
        assistantSend.addEventListener('click', () => {
            const input = document.getElementById('assistant-input');
            if (input) sendAssistantMessage(input.value.trim());
        });
    }
});

// === INTERACTIVE EXCEL EDITING ===
window.confirmColumnEdit = function (id, originalInstruction, guess, accepted) {
    // Hide buttons
    const container = document.getElementById(`confirm-${id}`);
    if (container) {
        const buttonsNode = container.querySelector('.flex.gap-2');
        if (buttonsNode) buttonsNode.style.display = 'none';

        let feedbackHTML = accepted
            ? `<p class="text-xs text-indigo-300 mt-2 font-bold max-w-[90%]"><i class="fa-solid fa-check mr-1"></i> Yes, applying to '${guess}'...</p>`
            : `<p class="text-xs text-white/50 mt-2 font-bold max-w-[90%]"><i class="fa-solid fa-xmark mr-1"></i> No, creating a new column...</p>`;
        container.innerHTML += feedbackHTML;
    }

    // Formulate a strict instruction so the AI doesn't guess again
    const newInstruction = accepted
        ? `I meant the column '${guess}'. Do this strictly to '${guess}': ${originalInstruction}`
        : `Do NOT use the column '${guess}'. I want a brand new column. STRICTLY ${originalInstruction}`;

    // Feed it right back into the execute pipeline
    executeAssistantAction('edit_excel', { instruction: newInstruction });
};
