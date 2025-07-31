// Modal Synchronization - Versão Robusta
document.addEventListener('DOMContentLoaded', function() {
    console.log('Modal Sync carregado...');
    
    // Função para sincronizar todos os campos
    function syncAllFields() {
        // Título
        const titleInput = document.getElementById('modal_title');
        const titleHidden = document.getElementById('modal_title_hidden');
        if (titleInput && titleHidden) {
            titleHidden.value = titleInput.value || '';
            console.log('Título sincronizado:', titleHidden.value);
        }
        
        // Descrição
        const descInput = document.getElementById('modal_description');
        const descHidden = document.getElementById('modal_description_hidden');
        if (descInput && descHidden) {
            descHidden.value = descInput.value || '';
            console.log('Descrição sincronizada:', descHidden.value);
        }
        
        // Data
        const dateInput = document.getElementById('modal_date_input');
        const dateHidden = document.getElementById('modal_date_hidden');
        if (dateInput && dateHidden) {
            if (!dateInput.value) {
                // Se não tem valor, usar hoje
                const today = new Date().toISOString().split('T')[0];
                dateInput.value = today;
            }
            dateHidden.value = dateInput.value;
            console.log('Data sincronizada:', dateHidden.value);
        }
        
        // Hora início
        const startInput = document.getElementById('modal_start_input');
        const startHidden = document.getElementById('modal_start_hidden');
        if (startInput && startHidden) {
            if (!startInput.value) {
                // Se não tem valor, usar próxima hora
                const now = new Date();
                now.setHours(now.getHours() + 1, 0, 0, 0);
                startInput.value = now.toTimeString().substr(0, 5);
            }
            startHidden.value = startInput.value;
            console.log('Hora início sincronizada:', startHidden.value);
        }
        
        // Hora fim
        const endInput = document.getElementById('modal_end_input');
        const endHidden = document.getElementById('modal_end_hidden');
        if (endInput && endHidden) {
            if (!endInput.value && startInput && startInput.value) {
                // Se não tem valor, usar +1 hora do início
                const [hours, minutes] = startInput.value.split(':');
                const endTime = new Date();
                endTime.setHours(parseInt(hours) + 1, parseInt(minutes));
                endInput.value = endTime.toTimeString().substr(0, 5);
            }
            endHidden.value = endInput.value;
            console.log('Hora fim sincronizada:', endHidden.value);
        }
        
        // All day
        const allDayInput = document.getElementById('modal_all_day');
        const allDayHidden = document.getElementById('modal_all_day_hidden');
        if (allDayInput && allDayHidden) {
            allDayHidden.value = allDayInput.checked ? '1' : '';
            console.log('All day sincronizado:', allDayHidden.value);
        }
    }
    
    // Sincronizar quando modal abre
    const modal = document.getElementById('novaReuniaoModal');
    if (modal) {
        modal.addEventListener('shown.bs.modal', function() {
            console.log('Modal aberto, sincronizando campos...');
            syncAllFields();
        });
    }
    
    // Sincronizar no submit (garantia)
    const form = document.getElementById('modalEventForm');
    if (form) {
        form.addEventListener('submit', function(e) {
            syncAllFields();

            const dateInput  = document.getElementById('modal_date_input');
            const titleInput = document.getElementById('modal_title');

            if (!dateInput.value) {
            alert('Erro: Data não foi preenchida!');
            e.preventDefault();
            return false;
            }
            if (!titleInput.value) {
            alert('Erro: Título é obrigatório!');
            e.preventDefault();
            return false;
            }
            // se chegar aqui, tudo ok
            console.log('Formulário validado, enviando...');
        });
        }

    
    // Adicionar eventos de input em tempo real
    setTimeout(function() {
        const titleInput = document.getElementById('modal_title');
        const descInput = document.getElementById('modal_description');
        const dateInput = document.getElementById('modal_date_input');
        const startInput = document.getElementById('modal_start_input');
        const endInput = document.getElementById('modal_end_input');
        const allDayInput = document.getElementById('modal_all_day');
        
        if (titleInput) {
            titleInput.addEventListener('input', syncAllFields);
            titleInput.addEventListener('blur', syncAllFields);
        }
        
        if (descInput) {
            descInput.addEventListener('input', syncAllFields);
            descInput.addEventListener('blur', syncAllFields);
        }
        
        if (dateInput) {
            dateInput.addEventListener('change', syncAllFields);
        }
        
        if (startInput) {
            startInput.addEventListener('change', syncAllFields);
        }
        
        if (endInput) {
            endInput.addEventListener('change', syncAllFields);
        }
        
        if (allDayInput) {
            allDayInput.addEventListener('change', syncAllFields);
        }
    }, 500);
});