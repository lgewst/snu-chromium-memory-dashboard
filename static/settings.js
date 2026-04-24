/**
 * @file settings.js
 * @description Manages the application settings UI. Handles loading, editing, 
 *              and persisting configuration for both local and remote (SSH) execution.
 *              Includes a directory browser for path selection.
 */

document.addEventListener('DOMContentLoaded', () => {
    const settingsForm = document.getElementById('settingsForm');
    const useSshCheckbox = document.getElementById('use_ssh');
    const sshSection = document.getElementById('sshSection');
    const saveStatus = document.getElementById('saveStatus');

    /**
     * Toggles SSH configuration section visibility based on the checkbox state.
     */
    useSshCheckbox.addEventListener('change', () => {
        sshSection.style.display = useSshCheckbox.checked ? 'block' : 'none';
    });

    /**
     * Loads the current application settings from the backend API.
     * Populates all form fields and initializes visibility of conditional sections.
     */
    fetch('/api/settings')
        .then(res => res.json())
        .then(data => {
            // Fill basic settings
            document.getElementById('local_chromium_path').value = data.local_chromium_path || '';
            document.getElementById('build_path').value = data.build_path || '';
            document.getElementById('depot_tools_path').value = data.depot_tools_path || '';
            document.getElementById('headless').checked = !!data.headless;
            document.getElementById('use_ssh').checked = !!data.use_ssh;
            document.getElementById('default_repeats').value = data.default_repeats || 5;
            document.getElementById('stabilization_seconds').value = data.stabilization_seconds || 20;
            document.getElementById('evaluation_seconds').value = data.evaluation_seconds || 20;
            document.getElementById('measurement_interval').value = data.measurement_interval || 1.0;

            // Trigger conditional section visibility
            sshSection.style.display = data.use_ssh ? 'block' : 'none';

            // Fill SSH specific configuration
            if (data.ssh_config) {
                document.getElementById('ssh_host').value = data.ssh_config.host || '';
                document.getElementById('ssh_port').value = data.ssh_config.port || 22;
                document.getElementById('ssh_user').value = data.ssh_config.user || '';
                document.getElementById('ssh_password').value = data.ssh_config.password || '';
                document.getElementById('ssh_chromium_path').value = data.ssh_config.chromium_path || '';
                document.getElementById('ssh_build_path').value = data.ssh_config.build_path || 'out/Default';
                document.getElementById('ssh_depot_tools_path').value = data.ssh_config.depot_tools_path || '';
            }
        });

    const saveButton = document.querySelector('#settingsForm button[type="submit"]');
    let isDirty = false;

    /**
     * Updates the "dirty" state of the form. Enables/disables the save button
     * based on whether changes have been made.
     * @param {boolean} dirty - Whether the form has unsaved changes.
     */
    const setDirty = (dirty) => {
        isDirty = dirty;
        saveButton.disabled = !dirty;
    };

    // Initially disabled until user input is detected
    saveButton.disabled = true;

    // Track input changes to set dirty state
    settingsForm.addEventListener('input', () => {
        setDirty(true);
    });

    // Special case for checkboxes and other non-input change events
    settingsForm.addEventListener('change', () => {
        setDirty(true);
    });

    /**
     * Global keyboard shortcuts (e.g., Ctrl+S to save).
     */
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault();
            if (isDirty) {
                settingsForm.dispatchEvent(new Event('submit'));
            }
        }
    });

    /**
     * Displays a browser warning if the user attempts to navigate away with unsaved changes.
     */
    window.addEventListener('beforeunload', (e) => {
        if (isDirty) {
            e.preventDefault();
            e.returnValue = ''; // Standard browser requirement
        }
    });

    /**
     * Handles form submission. Collects data from the form and sends it to the server.
     * Updates the UI to show save status success/failure.
     */
    settingsForm.addEventListener('submit', (e) => {
        e.preventDefault();
        saveStatus.textContent = 'Saving...';

        const formData = new FormData(settingsForm);
        const settings = {
            local_chromium_path: formData.get('local_chromium_path'),
            build_path: formData.get('build_path'),
            depot_tools_path: formData.get('depot_tools_path'),
            headless: formData.get('headless') === 'on',
            use_ssh: formData.get('use_ssh') === 'on',
            default_repeats: parseInt(formData.get('default_repeats')),
            stabilization_seconds: parseInt(formData.get('stabilization_seconds')),
            evaluation_seconds: parseInt(formData.get('evaluation_seconds')),
            measurement_interval: parseFloat(formData.get('measurement_interval')),
            ssh_config: {
                host: formData.get('ssh_host'),
                port: parseInt(formData.get('ssh_port')),
                user: formData.get('ssh_user'),
                password: formData.get('ssh_password'),
                chromium_path: formData.get('ssh_chromium_path'),
                build_path: formData.get('ssh_build_path'),
                depot_tools_path: formData.get('ssh_depot_tools_path')
            }
        };

        fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        })
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success') {
                saveStatus.textContent = 'Settings saved successfully!';
                saveStatus.style.color = 'var(--success-color)';
                setDirty(false);
            } else {
                saveStatus.textContent = 'Error: ' + data.message;
                saveStatus.style.color = 'var(--danger-color)';
            }
            setTimeout(() => { saveStatus.textContent = ''; }, 3000);
        });
    });

    // --- File Browser Modal Logic ---
    const modal = document.getElementById('fileBrowserModal');
    const closeBtn = document.querySelector('.close-modal');
    const fileList = document.getElementById('fileList');
    const currentPathDisplay = document.getElementById('currentPathDisplay');
    const selectDirBtn = document.getElementById('selectDirBtn');
    let currentTargetInputId = '';
    let currentBrowsingPath = '';

    /**
     * Binds click events to browse buttons to open the directory browser modal.
     */
    document.querySelectorAll('.browse-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            currentTargetInputId = btn.getAttribute('data-target');
            const initialPath = document.getElementById(currentTargetInputId).value || '.';
            openBrowser(initialPath);
        });
    });

    /**
     * Modal closing handlers (X button and background click).
     */
    closeBtn.onclick = () => modal.style.display = 'none';
    window.onclick = (e) => { if (e.target == modal) modal.style.display = 'none'; };

    /**
     * Opens the directory browser modal.
     * @param {string} path - The starting path to browse.
     */
    function openBrowser(path) {
        modal.style.display = 'block';
        fetchItems(path);
    }

    /**
     * Fetches directory items from the backend for the given path.
     * Updates the modal UI with files/folders.
     * @param {string} path - The filesystem path to fetch contents from.
     */
    function fetchItems(path) {
        fetch(`/api/browse?path=${encodeURIComponent(path)}`)
            .then(res => res.json())
            .then(data => {
                currentBrowsingPath = data.current_path;
                currentPathDisplay.textContent = currentBrowsingPath;
                fileList.innerHTML = '';
                
                // Note: Filtered to only show directories in this UI
                data.items.forEach(item => {
                    const li = document.createElement('li');
                    li.innerHTML = `📁 ${item.name}`;
                    li.onclick = () => fetchItems(item.path);
                    fileList.appendChild(li);
                });
            });
    }

    /**
     * Confirms selection of the currently browsed directory.
     * Sets the value back to the originating input field.
     */
    selectDirBtn.onclick = () => {
        document.getElementById(currentTargetInputId).value = currentBrowsingPath;
        setDirty(true);
        modal.style.display = 'none';
    };
});
