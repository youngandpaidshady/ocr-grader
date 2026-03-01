/**
 * QSI Smart Grader — Enhancement Layer
 * Adds: multi-class picker, image compression, contextual guides,
 * tabbed results, duplicate detection, AI safety net, smart assistant
 */

// ═══════════════════════════════════════════════
//  SESSION CLASS TRACKING
// ═══════════════════════════════════════════════
const sessionClasses = new Set();

function addToSessionClasses(className) {
    if (className && className.trim()) {
        sessionClasses.add(className.trim());
    }
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
        message: '👋 Welcome! If you have the Excel from last time, tap <strong>"I Have My Excel"</strong>. First time? Tap <strong>"Start Fresh"</strong>.',
        target: '#landing-phase',
        position: 'center'
    },
    'class-picker': {
        message: '☝️ Check all the classes you\'re grading today. You can scan one class at a time or mix — the AI sorts them automatically!',
        target: '#class-picker-container',
        position: 'below'
    },
    'subject': {
        message: '📘 What subject are you teaching? This keeps each subject\'s scores separate in your file.',
        target: '#subject-name',
        position: 'below'
    },
    'assessment': {
        message: '📝 What kind of test is this? e.g. 1st CA, 2nd CA, Exam. This labels the score column.',
        target: '#assessment-type',
        position: 'below'
    },
    'camera': {
        message: '📸 Point your camera at the graded script. Hold steady — it\'ll auto-snap when ready! Tap the shutter for manual.',
        target: '#btn-capture',
        position: 'above'
    },
    'review': {
        message: '✅ Check each student\'s name and score. Tap any field to fix mistakes. When everything looks good, tap <strong>"Save"</strong> below.',
        target: '#review-section',
        position: 'top'
    },
    'after-save': {
        message: '🎉 Done! Tap <strong>"Download"</strong> for your updated Excel file. Keep this file safe — it\'s your master copy for next time!',
        target: '#btn-download-sheet',
        position: 'above'
    }
};

function showGuide(guideKey) {
    // Don't re-show dismissed guides in this session
    if (sessionStorage.getItem('guide_' + guideKey)) return;

    const guide = guideMessages[guideKey];
    if (!guide) return;

    // Remove any existing guide
    const existing = document.querySelector('.guide-popup');
    if (existing) existing.remove();

    const popup = document.createElement('div');
    popup.className = 'guide-popup';
    popup.innerHTML = `
        <div class="guide-popup-content">
            <div class="guide-popup-message">${guide.message}</div>
            <button class="guide-popup-dismiss" onclick="dismissGuide('${guideKey}', this)">
                Got it 👍
            </button>
        </div>
        <div class="guide-popup-arrow"></div>
    `;

    document.body.appendChild(popup);

    // Position near target
    const target = document.querySelector(guide.target);
    if (target) {
        const rect = target.getBoundingClientRect();
        const scrollY = window.scrollY;

        if (guide.position === 'center') {
            popup.style.top = (rect.top + scrollY + rect.height / 2) + 'px';
            popup.style.left = '50%';
            popup.style.transform = 'translate(-50%, -50%)';
        } else if (guide.position === 'below') {
            popup.style.top = (rect.bottom + scrollY + 12) + 'px';
            popup.style.left = Math.max(16, rect.left) + 'px';
        } else if (guide.position === 'above') {
            popup.style.bottom = (window.innerHeight - rect.top - scrollY + 12) + 'px';
            popup.style.left = Math.max(16, rect.left) + 'px';
        } else {
            popup.style.top = (rect.top + scrollY) + 'px';
            popup.style.left = '50%';
            popup.style.transform = 'translateX(-50%)';
        }

        // Add pulsing indicator to target
        target.classList.add('guide-pulse');
    }

    // Auto-dismiss after 10s
    setTimeout(() => {
        if (popup.parentNode) dismissGuide(guideKey, popup.querySelector('.guide-popup-dismiss'));
    }, 10000);
}

function dismissGuide(guideKey, btn) {
    sessionStorage.setItem('guide_' + guideKey, '1');
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
    if (!container) return;

    // Fetch classes from API
    fetch('/api/classes')
        .then(r => r.json())
        .then(classes => {
            renderClassChips(container, classes);
        })
        .catch(() => {
            container.innerHTML = '<p class="text-muted-foreground text-sm">Could not load classes.</p>';
        });
}

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
    const subjectSelect = document.getElementById('subject-name');
    if (subjectSelect && subjectSelect.value && selectedClasses.length > 0) {
        checkEnrollmentForClasses();
    }
}

// ═══════════════════════════════════════════════
//  ENROLLMENT CHECK (Workstream 3 Layer 3)
// ═══════════════════════════════════════════════
function checkEnrollmentForClasses() {
    const subjectSelect = document.getElementById('subject-name');
    const subjectName = subjectSelect ? (subjectSelect.value === 'custom'
        ? document.getElementById('custom-subject')?.value?.trim()
        : subjectSelect.value) : '';

    if (!subjectName || selectedClasses.length === 0) return;

    // Show enrollment confirmation for each selected class
    selectedClasses.forEach(className => {
        const key = `enrollment_${className}_${subjectName}`;
        if (sessionStorage.getItem(key)) return; // Already confirmed

        showEnrollmentPrompt(className, subjectName);
    });
}

function showEnrollmentPrompt(className, subjectName) {
    const existing = document.querySelector(`.enrollment-prompt[data-class="${className}"]`);
    if (existing) return;

    const container = document.getElementById('enrollment-prompts') || createEnrollmentContainer();
    const prompt = document.createElement('div');
    prompt.className = 'enrollment-prompt animate-fade-in-up';
    prompt.dataset.class = className;
    prompt.innerHTML = `
        <div class="flex items-center gap-3 p-4 bg-indigo-500/10 border border-indigo-500/20 rounded-xl">
            <i class="fa-solid fa-users text-indigo-400 text-lg"></i>
            <div class="flex-1">
                <p class="text-sm font-bold text-white">${className} — ${subjectName}</p>
                <p class="text-xs text-indigo-300/70 mt-0.5">Do all students in this class take ${subjectName}?</p>
            </div>
            <div class="flex gap-2">
                <button onclick="confirmEnrollment('${className}', '${subjectName}', true, this)"
                    class="px-3 py-1.5 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 text-xs font-bold rounded-lg transition-all border border-emerald-500/30">
                    Yes, all
                </button>
                <button onclick="openSelectiveEnrollment('${className}', '${subjectName}')"
                    class="px-3 py-1.5 bg-white/5 hover:bg-white/10 text-white text-xs font-bold rounded-lg transition-all border border-white/10">
                    Let me pick
                </button>
            </div>
        </div>
    `;
    container.appendChild(prompt);
}

function createEnrollmentContainer() {
    const c = document.createElement('div');
    c.id = 'enrollment-prompts';
    c.className = 'space-y-3 mb-5';
    const assessmentContainer = document.getElementById('assessment-type-container');
    if (assessmentContainer) {
        assessmentContainer.parentNode.insertBefore(c, assessmentContainer);
    }
    return c;
}

function confirmEnrollment(className, subjectName, allEnrolled, btn) {
    sessionStorage.setItem(`enrollment_${className}_${subjectName}`, allEnrolled ? 'all' : 'selective');
    const prompt = btn.closest('.enrollment-prompt');
    if (prompt) {
        prompt.classList.add('opacity-0', 'scale-95');
        setTimeout(() => prompt.remove(), 300);
    }
}

function openSelectiveEnrollment(className, subjectName) {
    // Trigger the existing selective enrollment modal
    const targetClassSelect = document.getElementById('target-class');
    if (targetClassSelect) {
        targetClassSelect.value = className;
        targetClassSelect.dispatchEvent(new Event('change'));
    }
    // Open the paste/enrollment modal via the existing mechanism
    const pasteBtn = document.getElementById('btn-open-paste-modal');
    if (pasteBtn) pasteBtn.click();

    sessionStorage.setItem(`enrollment_${className}_${subjectName}`, 'selective');
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
//  TABBED RESULTS (Workstream 5)
// ═══════════════════════════════════════════════
let sheetsData = null;
let activeTab = null;

function renderResultsTabs(sheets) {
    sheetsData = sheets;
    const analyticsSection = document.getElementById('analytics-section');
    if (!analyticsSection) return;

    // Remove old tab bar if exists
    const oldTabBar = analyticsSection.querySelector('.results-tab-bar');
    if (oldTabBar) oldTabBar.remove();

    // Remove old table container if exists
    const oldTable = analyticsSection.querySelector('.results-tab-content');
    if (oldTable) oldTable.remove();

    const sheetNames = Object.keys(sheets);
    if (sheetNames.length === 0) return;

    // Create tab bar
    const tabBar = document.createElement('div');
    tabBar.className = 'results-tab-bar';
    tabBar.innerHTML = `<div class="results-tabs-scroll">${sheetNames.map((name, i) => {
        const sheet = sheets[name];
        return `<button class="results-tab ${i === 0 ? 'results-tab-active' : ''}" 
                data-sheet="${name}" onclick="switchResultsTab('${name.replace(/'/g, "\\'")}', this)">
                <span class="results-tab-class">${sheet.class}</span>
                <span class="results-tab-subject">${sheet.subject}</span>
            </button>`;
    }).join('')
        }</div>`;

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

    // Build table
    if (!sheet.rows || sheet.rows.length === 0) {
        container.innerHTML = '<p class="text-muted-foreground text-sm text-center py-8">No data for this class yet.</p>';
        return;
    }

    const columns = sheet.columns || Object.keys(sheet.rows[0] || {});

    container.innerHTML = `
        <div class="overflow-x-auto rounded-xl border border-white/5">
            <table class="w-full text-sm">
                <thead>
                    <tr class="border-b border-white/10 bg-black/30">
                        ${columns.map(col => `<th class="px-4 py-3 text-left text-xs font-bold text-muted-foreground uppercase tracking-widest whitespace-nowrap">${col}</th>`).join('')}
                    </tr>
                </thead>
                <tbody>
                    ${sheet.rows.map((row, i) => `
                        <tr class="border-b border-white/5 hover:bg-white/5 transition-colors ${i % 2 === 0 ? 'bg-black/10' : ''}">
                            ${columns.map(col => `<td class="px-4 py-3 text-white font-medium whitespace-nowrap">${row[col] ?? ''}</td>`).join('')}
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
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

    // Store context from parsed Excel
    assistantContext = {
        classes: parsedData?.classes || [],
        subjects: parsedData?.subjects || [],
        students: parsedData?.studentCount || 0,
        assessments: parsedData?.assessments || []
    };

    // Build summary
    const summaryEl = modal.querySelector('#assistant-summary');
    if (summaryEl && parsedData) {
        const classesList = parsedData.classes?.join(', ') || 'Unknown';
        const assessments = parsedData.assessments?.join(', ') || 'None detected';
        summaryEl.innerHTML = `
            <div class="flex items-start gap-3 p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-xl">
                <i class="fa-solid fa-file-excel text-emerald-400 text-2xl"></i>
                <div>
                    <p class="text-sm font-bold text-white">File analyzed successfully!</p>
                    <p class="text-xs text-emerald-300/70 mt-1">
                        Found <strong>${parsedData.studentCount || 0} students</strong> in 
                        <strong>${classesList}</strong> with <strong>${assessments}</strong> scores.
                    </p>
                </div>
            </div>
        `;
    }

    // Clear chat history
    assistantHistory = [];
    const chatEl = modal.querySelector('#assistant-chat');
    if (chatEl) chatEl.innerHTML = '';

    modal.classList.remove('hidden');
    modal.classList.add('flex');
}

async function sendAssistantMessage(message) {
    if (!message || !message.trim()) return;

    const chatEl = document.getElementById('assistant-chat');
    const inputEl = document.getElementById('assistant-input');
    if (!chatEl) return;

    // Add user message
    chatEl.innerHTML += `
        <div class="flex justify-end mb-3">
            <div class="bg-primary/20 border border-primary/30 rounded-2xl rounded-br-md px-4 py-2 max-w-[80%]">
                <p class="text-sm text-white">${message}</p>
            </div>
        </div>
    `;

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
        const response = await fetch('/api/smart-assistant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message,
                context: assistantContext,
                currentScreen: currentScreen
            })
        });
        const data = await response.json();

        // Remove typing indicator
        document.getElementById('assistant-typing')?.remove();

        // Add assistant response
        let actionBtn = '';
        if (data.action && data.action !== 'none') {
            actionBtn = `
                <button onclick="executeAssistantAction('${data.action}', ${JSON.stringify(data.params || {}).replace(/"/g, '&quot;')})"
                    class="mt-2 px-3 py-1.5 bg-primary/20 hover:bg-primary/30 text-primary text-xs font-bold rounded-lg transition-all border border-primary/30 inline-flex items-center gap-1.5">
                    <i class="fa-solid fa-play text-[10px]"></i> Do this
                </button>
            `;
        }

        chatEl.innerHTML += `
            <div class="flex justify-start mb-3">
                <div class="bg-white/5 border border-white/10 rounded-2xl rounded-bl-md px-4 py-2 max-w-[80%]">
                    <p class="text-sm text-white">${data.response || 'I\'m not sure how to help with that.'}</p>
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

function executeAssistantAction(action, params) {
    const modal = document.getElementById('smart-assistant-modal');

    switch (action) {
        case 'setup_session': {
            // Smart setup: pre-fill class, subject, assessment and navigate
            if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }

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
            const existingAssessments = assistantContext.assessments || [];
            const classes = assistantContext.classes || [];
            let prompt = '';
            if (existingAssessments.length > 0) {
                prompt = `I want to grade the next test. I already have ${existingAssessments.join(', ')} scores for ${classes.join(', ')}. What should I grade next?`;
            } else {
                prompt = `I want to start grading tests for ${classes.length > 0 ? classes.join(', ') : 'my class'}. Help me set up.`;
            }
            sendAssistantMessage(prompt);
            break;
        }
        case 'edit_scores': {
            const classes = assistantContext.classes || [];
            sendAssistantMessage(`I need to edit some scores for ${classes.length > 0 ? classes.join(', ') : 'my students'}. How do I do that?`);
            break;
        }
        case 'view_standings': {
            const classes = assistantContext.classes || [];
            if (classes.length > 0) {
                sendAssistantMessage(`Show me the current standings for ${classes.join(', ')}.`);
            } else {
                if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
                document.getElementById('btn-export')?.click();
            }
            break;
        }
        case 'add_student': {
            if (params?.student_name && params?.class_name) {
                safeAddStudent(params.student_name, params.class_name);
            } else {
                sendAssistantMessage('I want to add a new student to one of my class rosters.');
            }
            break;
        }
        case 'add_class':
            if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
            document.getElementById('btn-open-paste-modal')?.click();
            break;
        case 'manage_enrollment':
            sendAssistantMessage('I need to update which students take my subject.');
            break;
        case 'export_data':
            if (modal) { modal.classList.add('hidden'); modal.classList.remove('flex'); }
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

function closeSmartAssistant() {
    const modal = document.getElementById('smart-assistant-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
}

// ═══════════════════════════════════════════════
//  INITIALIZATION — Hook into existing app
// ═══════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    // Show landing guide on first visit
    setTimeout(() => showGuide('landing'), 1500);

    // Track class selection from the old dropdown too
    const targetClass = document.getElementById('target-class');
    if (targetClass) {
        targetClass.addEventListener('change', () => {
            addToSessionClasses(targetClass.value);
        });
    }

    // Track subject selection for enrollment check
    const subjectSelect = document.getElementById('subject-name');
    if (subjectSelect) {
        subjectSelect.addEventListener('change', () => {
            if (selectedClasses.length > 0) {
                setTimeout(() => showGuide('assessment'), 500);
                checkEnrollmentForClasses();
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
