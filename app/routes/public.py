# app/routes/public.py
from flask import (
    Blueprint, render_template, redirect, url_for, flash, session,
)
from flask_mail import Message
from app import db, mail
from app.forms import (
    SignupForm, ReenviarVerificacionForm,
    SolicitarResetForm, ResetPasswordForm,
)
from app.utils import (
    generar_token_verificacion,
    verificar_token_verificacion,
    generar_token_reset_password,
    verificar_token_reset_password,
)
from app.models import Usuario
from app.services.empresa_service import EmpresaService
from app import limiter

public_bp = Blueprint('public', __name__)


@public_bp.route('/signup', methods=['GET', 'POST'])
@limiter.limit("5 per hour", methods=["POST"])
@limiter.limit("20 per day", methods=["POST"])
def signup():
    if session.get('usuario_id'):
        return redirect(url_for('main.index'))

    form = SignupForm()
    if form.validate_on_submit():
        from app.utils_db import transaccion
        try:
            with transaccion():
                result = EmpresaService.crear_empresa_completa(
                    nombre=form.nombre_club.data,
                    cuit=form.cuit.data,
                    email_admin=form.email.data,
                    password=form.password.data,
                    nombre_admin=form.nombre_admin.data,
                )
            admin = result['admin']

            # Generar token y enviar correo
            token = generar_token_verificacion(admin.username)
            link_verificacion = url_for(
                'public.verificar_email', token=token, _external=True
            )

            try:
                msg = Message(
                    subject='Verificá tu correo — Club Manager',
                    recipients=[admin.username],
                    html=render_template(
                        'email/verificacion.html', link=link_verificacion
                    ),
                )
                mail.send(msg)
                flash(
                    f'Te enviamos un correo a {admin.username}. '
                    'Hacé clic en el enlace para activar tu cuenta. '
                    'Si no lo recibís, podés pedir uno nuevo desde "Reenviar verificación".',
                    'info',
                )
            except Exception as mail_err:
                # Si el correo falla, al menos logueamos el error.
                # Podés también guardar el token en el usuario para reintento.
                flash(
                    'La cuenta se creó, pero no pudimos enviar el correo '
                    f'de verificación. Contactá al soporte. ({mail_err})',
                    'warning',
                )

            return redirect(url_for('auth.login'))

        except ValueError as e:
            flash(str(e), 'danger')
        except Exception as e:
            flash(f'Error inesperado al crear el club: {str(e)}', 'danger')

    return render_template('public/signup.html', form=form)


@public_bp.route('/verificar/<token>')
@limiter.limit("30 per hour")
def verificar_email(token):
    email = verificar_token_verificacion(token)
    if not email:
        flash('El enlace de verificación es inválido o ya expiró.', 'danger')
        return redirect(url_for('auth.login'))

    # Con multiempresa, puede haber varios usuarios con el mismo email.
    # Verificamos el más reciente que esté pendiente.
    usuario = (
        Usuario.query
        .filter_by(username=email, email_verificado=False)
        .order_by(Usuario.id.desc())
        .first()
    )

    if not usuario:
        ya_verificado = Usuario.query.filter_by(
            username=email, email_verificado=True
        ).first()
        if ya_verificado:
            flash('Tu correo ya estaba verificado. Podés iniciar sesión.',
                  'info')
            return redirect(url_for('auth.login'))
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('auth.login'))

    usuario.email_verificado = True
    usuario.activo = True
    db.session.commit()

    flash('¡Correo verificado! Ya podés iniciar sesión.', 'success')
    return redirect(url_for('auth.login'))


@public_bp.route('/reenviar-verificacion', methods=['GET', 'POST'])
@limiter.limit("3 per 15 minutes", methods=["POST"])
@limiter.limit("10 per day", methods=["POST"])
def reenviar_verificacion():
    if session.get('usuario_id'):
        return redirect(url_for('main.index'))

    form = ReenviarVerificacionForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        # Buscar el más reciente sin verificar (multiempresa)
        usuario = (
            Usuario.query
            .filter_by(username=email, email_verificado=False)
            .order_by(Usuario.id.desc())
            .first()
        )

        if usuario:
            token = generar_token_verificacion(usuario.username)
            link = url_for('public.verificar_email', token=token, _external=True)
            try:
                msg = Message(
                    subject='Verificá tu correo — Club Manager',
                    recipients=[usuario.username],
                    html=render_template(
                        'email/verificacion.html', link=link
                    ),
                )
                mail.send(msg)
            except Exception:
                flash(
                    'No pudimos enviar el correo. Reintentá en unos minutos.',
                    'danger',
                )
                return render_template(
                    'public/reenviar_verificacion.html', form=form
                )

        flash(
            'Si el correo está registrado y pendiente de verificación, '
            'te enviamos un nuevo enlace. Revisá tu bandeja (y el spam).',
            'info',
        )
        return redirect(url_for('auth.login'))

    return render_template('public/reenviar_verificacion.html', form=form)

@public_bp.route('/olvide-password', methods=['GET', 'POST'])
@limiter.limit("3 per 15 minutes", methods=["POST"])
@limiter.limit("10 per day", methods=["POST"])
def solicitar_reset_password():
    if session.get('usuario_id'):
        return redirect(url_for('main.index'))

    form = SolicitarResetForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        usuario = Usuario.query.filter_by(username=email).first()

        # Por seguridad, siempre respondemos lo mismo
        if usuario:
            token = generar_token_reset_password(usuario.username)
            link = url_for(
                'public.reset_password', token=token, _external=True
            )
            try:
                msg = Message(
                    subject='Recuperar contraseña — Club Manager',
                    recipients=[usuario.username],
                    html=render_template(
                        'email/reset_password.html', link=link
                    ),
                )
                mail.send(msg)
            except Exception:
                flash(
                    'No pudimos enviar el correo. Reintentá en unos minutos.',
                    'danger',
                )
                return render_template(
                    'public/solicitar_reset.html', form=form
                )

        flash(
            'Si el correo está registrado, te enviamos un enlace para '
            'recuperar tu contraseña. Revisá tu bandeja (y el spam).',
            'info',
        )
        return redirect(url_for('auth.login'))

    return render_template('public/solicitar_reset.html', form=form)


@public_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
@limiter.limit("30 per hour")
def reset_password(token):
    email = verificar_token_reset_password(token)
    if not email:
        flash(
            'El enlace para recuperar la contraseña es inválido o ya expiró. '
            'Pedí uno nuevo.',
            'danger',
        )
        return redirect(url_for('public.solicitar_reset_password'))

    usuarios = Usuario.query.filter_by(username=email).all()
    if not usuarios:
        flash('Usuario no encontrado.', 'danger')
        return redirect(url_for('auth.login'))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        # Multiempresa: si el email existe en varios clubes, se resetea
        # la contraseña en todos (es la misma persona).
        for usuario in usuarios:
            usuario.set_password(form.password.data)
            if not usuario.email_verificado:
                usuario.email_verificado = True
            usuario.activo = True
        db.session.commit()

        flash(
            '¡Contraseña actualizada! Ya podés iniciar sesión.',
            'success',
        )
        return redirect(url_for('auth.login'))

    return render_template(
        'public/reset_password.html', form=form, token=token
    )